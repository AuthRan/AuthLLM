"""AshuGPT: the full decoder-only Transformer language model.

Wires together everything built so far: a token embedding table, a stack of
`config.n_layers` TransformerBlocks (each an RMSNorm + RoPE-based causal
self-attention + RMSNorm + SwiGLU FFN, with residual connections), a final
RMSNorm, and a linear language-modeling head that projects back to
vocabulary logits.

    input_ids: (batch, seq_len)               int64 token ids
        -> token_embedding                     (batch, seq_len, d_model)
        -> n_layers x TransformerBlock          (batch, seq_len, d_model)
        -> final_norm                           (batch, seq_len, d_model)
        -> lm_head                              (batch, seq_len, vocab_size)
    logits: (batch, seq_len, vocab_size)

## Next-token prediction and the one-token shift

Given the sentence "The cat sat down", a decoder-only LM is trained so that,
at every position, the model's prediction from everything up to and
including that position matches whatever token actually came next:

    input_ids: The  cat  sat        (3 tokens: positions 0, 1, 2)
    labels:    cat  sat  down       (3 tokens: positions 0, 1, 2)

`labels` is *not* a re-encoding of the same text -- it is `input_ids`
shifted one position into the future. `labels[t]` is "the token that should
come right after `input_ids[t]`", so:

    logits[:, 0, :] (computed causally from just "The")       should predict "cat"
    logits[:, 1, :] (computed causally from "The cat")        should predict "sat"
    logits[:, 2, :] (computed causally from "The cat sat")    should predict "down"

Because causal self-attention already guarantees logits[:, t, :] only saw
input_ids[:, :t+1] (see attention.py's causal mask), comparing logits[:, t,
:] against labels[:, t] directly -- with *no further shifting inside the
model* -- is exactly the next-token objective. The shift itself happens
once, upstream, when the data pipeline slices a token stream into
overlapping windows: `input_ids = tokens[i : i+L]`, `labels = tokens[i+1 :
i+L+1]`. This is the same convention nanoGPT's `get_batch()` uses; it's
different from (e.g.) Hugging Face's `GPT2LMHeadModel`, which accepts
labels identical to input_ids and shifts internally -- we push that
responsibility to the data pipeline instead, which keeps forward() itself
simple: it's just an elementwise loss between logits[t] and labels[t].

Positions that must not contribute to the loss (e.g. padding) should be set
to -100 in `labels` -- PyTorch's `F.cross_entropy` skips index -100 by
default, so no extra masking logic is needed here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.checkpoint

from ashugpt.config import ModelConfig
from ashugpt.model.block import TransformerBlock
from ashugpt.model.norm import RMSNorm

KVCache = tuple[torch.Tensor, torch.Tensor]


@dataclass
class GPTOutput:
    logits: torch.Tensor  # (batch, seq_len, vocab_size)
    loss: torch.Tensor | None = None  # scalar; set only when labels are given
    kv_caches: list[KVCache] | None = None  # one (k, v) pair per layer


class AshuGPT(nn.Module):
    """Input: input_ids (batch, seq_len) int64. Output: logits (batch, seq_len, vocab_size)."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config

        self.token_embedding = nn.Embedding(config.vocab_size, config.d_model)
        self.blocks = nn.ModuleList(
            [
                TransformerBlock(
                    d_model=config.d_model,
                    n_heads=config.n_heads,
                    n_kv_heads=config.n_kv_heads,
                    d_ff=config.d_ff,
                    max_seq_len=config.context_length,
                    rope_theta=config.rope_theta,
                    norm_eps=config.norm_eps,
                )
                for _ in range(config.n_layers)
            ]
        )
        self.final_norm = RMSNorm(config.d_model, eps=config.norm_eps)
        self.lm_head = nn.Linear(config.d_model, config.vocab_size, bias=False)

        # Off by default -- see set_memory_optimizations() and README.md's
        # Memory Optimization section for what this trades off.
        self.gradient_checkpointing = False

        self.apply(self._init_weights)
        self._apply_residual_scaling()

        if config.tie_embeddings:
            # Share one tensor between input embedding and output projection
            # instead of learning two separate vocab_size x d_model matrices
            # -- halves the embedding parameter budget, which dominates
            # total size at small scales (see Milestone 1's tiny preset).
            self.lm_head.weight = self.token_embedding.weight

    @staticmethod
    def _init_weights(module: nn.Module) -> None:
        # GPT-2-style initialization: small Gaussian weights, zero bias.
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def _apply_residual_scaling(self) -> None:
        # Each block adds two things to the residual stream (attention
        # output, then FFN output), so activation variance would otherwise
        # compound across n_layers stacked additions. Scaling each
        # residual-stream projection's init by 1/sqrt(2 * n_layers)
        # counteracts that -- the same fix used in GPT-2's original
        # initialization scheme.
        residual_std = 0.02 / math.sqrt(2 * self.config.n_layers)
        for name, param in self.named_parameters():
            if name.endswith("o_proj.weight") or name.endswith("down_proj.weight"):
                nn.init.normal_(param, mean=0.0, std=residual_std)

    def set_memory_optimizations(
        self,
        gradient_checkpointing: bool | None = None,
        efficient_attention: bool | None = None,
    ) -> None:
        """Toggle memory/compute tradeoffs that change how much memory a
        forward+backward pass takes, not what it computes -- safe to flip
        at any point (before training starts, mid-run, or never, for pure
        inference) since neither option touches a learned parameter.
        `None` (the default for each) leaves that setting unchanged.

        See README.md's Memory Optimization section for what each trades
        off and what was actually measured.
        """
        if gradient_checkpointing is not None:
            self.gradient_checkpointing = gradient_checkpointing
        if efficient_attention is not None:
            for block in self.blocks:
                block.attn.use_efficient_attention = efficient_attention

    def num_parameters(self, exclude_embeddings: bool = False) -> int:
        """Exact parameter count of this constructed model. Compare against
        `ModelConfig.approx_param_count()`, which estimates the same number
        from shape alone, before any model is built."""
        total = sum(p.numel() for p in self.parameters())
        if exclude_embeddings:
            total -= self.token_embedding.weight.numel()
            if not self.config.tie_embeddings:
                total -= self.lm_head.weight.numel()
        return total

    def forward(
        self,
        input_ids: torch.Tensor,
        labels: torch.Tensor | None = None,
        kv_caches: list[KVCache] | None = None,
        position_offset: int = 0,
        segment_ids: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
    ) -> GPTOutput:
        """See the module docstring for the labels/shift convention.

        kv_caches / position_offset are plumbed straight through to every
        block (see TransformerBlock/CausalSelfAttention) purely so this
        model is already usable with incremental decoding once a KV-cache
        manager exists -- not exercised by training, which always passes
        kv_caches=None, position_offset=0.

        segment_ids / position_ids are the sequence-packing path: several
        independent examples share one window, so attention must not cross
        their boundaries and RoPE must restart each one at position 0. Both
        default to None, which is the unpacked behaviour this model has
        always had. See ashugpt/data/instruction.py's
        PackedInstructionDataset for what produces them.
        """
        batch, seq_len = input_ids.shape
        if seq_len > self.config.context_length:
            raise ValueError(
                f"sequence length ({seq_len}) exceeds this model's context_length ({self.config.context_length})"
            )

        x = self.token_embedding(input_ids)  # (batch, seq_len, d_model)

        # Gradient checkpointing only makes sense when there's a backward
        # pass to save memory for, and only when there's no KV cache to
        # keep consistent (checkpointing re-runs the block's forward from
        # scratch during backward, which would need to redo the cache
        # concatenation too -- generation never needs checkpointing anyway,
        # since it has no backward pass).
        use_checkpointing = self.gradient_checkpointing and self.training and kv_caches is None

        present_kv_caches: list[KVCache] = []
        for i, block in enumerate(self.blocks):
            layer_cache = kv_caches[i] if kv_caches is not None else None
            if use_checkpointing:
                # Trades recomputing this block's forward pass during
                # backward for not storing its activations during the
                # forward pass -- see README.md's Memory Optimization
                # section. use_reentrant=False is the modern, DDP-safe mode.
                x, present = torch.utils.checkpoint.checkpoint(
                    block,
                    x,
                    position_offset=position_offset,
                    segment_ids=segment_ids,
                    position_ids=position_ids,
                    use_reentrant=False,
                )
            else:
                x, present = block(
                    x,
                    kv_cache=layer_cache,
                    position_offset=position_offset,
                    segment_ids=segment_ids,
                    position_ids=position_ids,
                )
            present_kv_caches.append(present)

        x = self.final_norm(x)  # (batch, seq_len, d_model)
        logits = self.lm_head(x)  # (batch, seq_len, vocab_size)

        loss = None
        if labels is not None:
            loss = F.cross_entropy(logits.view(-1, self.config.vocab_size), labels.view(-1))

        return GPTOutput(logits=logits, loss=loss, kv_caches=present_kv_caches)
