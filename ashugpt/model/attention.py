"""Causal multi-head self-attention with RoPE.

Mathematical purpose: for each token, attention computes a weighted average
of *all other tokens'* value vectors, where the weights come from how well
that token's query matches each other token's key (scaled dot-product,
softmax-normalized). "Causal" means the weights for future positions are
forced to zero -- a token can only be influenced by itself and earlier
tokens, which is what makes autoregressive next-token prediction coherent
(training and generation see the same information pattern).

Q, K, V come from separate linear projections of the same input (no bias,
matching the rest of this architecture), split into `n_heads` independent
attention heads, rotated with RoPE, combined via masked scaled dot-product
attention, then merged back and projected once more.

The actual score/softmax/weighted-sum math is implemented manually by
default (readable, testable) with an optional fused-kernel path
(`use_efficient_attention`, off by default) -- see the Memory Optimization
section of README.md for what that trades off and what was measured.
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from ashugpt.model.rope import RotaryEmbedding, apply_rotary_pos_emb


def causal_mask(seq_len_q: int, seq_len_k: int, offset: int, device: torch.device) -> torch.Tensor:
    """Boolean mask, True where attention must be blocked (future positions).

    Query index i in this call sits at absolute position `offset + i` (the
    offset accounts for any already-cached tokens before it). It may attend
    to key index j iff j <= offset + i. With offset=0 and seq_len_q ==
    seq_len_k this is the standard upper-triangular causal mask; with a
    nonzero offset (a new token attending back through a KV cache) it
    correctly allows attending to every cached position plus itself.
    """
    q_positions = torch.arange(seq_len_q, device=device).unsqueeze(1) + offset  # (seq_len_q, 1)
    k_positions = torch.arange(seq_len_k, device=device).unsqueeze(0)  # (1, seq_len_k)
    return k_positions > q_positions  # (seq_len_q, seq_len_k), broadcasts over batch/heads


class CausalSelfAttention(nn.Module):
    """Input shape: (batch, seq_len, d_model). Output shape: (batch, seq_len, d_model)."""

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        n_kv_heads: int,
        max_seq_len: int,
        rope_theta: float = 10000.0,
    ) -> None:
        super().__init__()
        if d_model % n_heads != 0:
            raise ValueError(f"d_model ({d_model}) must be divisible by n_heads ({n_heads})")
        if n_heads % n_kv_heads != 0:
            raise ValueError(
                f"n_heads ({n_heads}) must be divisible by n_kv_heads ({n_kv_heads}) -- "
                f"every key/value head must serve the same number of query heads"
            )

        self.n_heads = n_heads
        self.n_kv_heads = n_kv_heads
        self.n_groups = n_heads // n_kv_heads  # query heads sharing one K/V head
        self.head_dim = d_model // n_heads

        # Q keeps one head per query head; K/V shrink to n_kv_heads. With
        # n_kv_heads == n_heads (the default) this is exactly the original
        # square projection, so nothing changes for existing configs.
        kv_dim = n_kv_heads * self.head_dim
        self.q_proj = nn.Linear(d_model, d_model, bias=False)
        self.k_proj = nn.Linear(d_model, kv_dim, bias=False)
        self.v_proj = nn.Linear(d_model, kv_dim, bias=False)
        self.o_proj = nn.Linear(d_model, d_model, bias=False)

        self.rope = RotaryEmbedding(self.head_dim, max_seq_len, theta=rope_theta)

        # Off by default: the manual math below stays the pedagogical
        # reference path. Flip via AshuGPT.set_memory_optimizations() --
        # see attention_efficient_path below for what this trades off.
        self.use_efficient_attention = False

    def forward(
        self,
        x: torch.Tensor,
        kv_cache: tuple[torch.Tensor, torch.Tensor] | None = None,
        position_offset: int = 0,
    ) -> tuple[torch.Tensor, tuple[torch.Tensor, torch.Tensor]]:
        """
        x: (batch, seq_len, d_model) -- the new tokens for this call only
            (the *whole* sequence during training; possibly just one new
            token during future cached generation).
        kv_cache: optional (past_k, past_v), each (batch, n_heads,
            past_len, head_dim) -- keys/values from previous calls.
        position_offset: absolute position of x[:, 0, :]. 0 during training;
            `past_len` when continuing from a cache.

        Returns (output, present_kv) where output has x's shape and
        present_kv is (k, v) including this call's new keys/values
        concatenated onto kv_cache -- pass it back in as kv_cache on the
        next call to continue generation without recomputation. This is
        what makes the module ready for a future KV-cache manager (SPEC.md
        M10) without building that manager here.
        """
        batch, seq_len, d_model = x.shape

        q = self.q_proj(x)  # (batch, seq_len, d_model)
        k = self.k_proj(x)
        v = self.v_proj(x)

        # (batch, seq_len, d_model) -> (batch, seq_len, n_heads, head_dim) -> (batch, n_heads, seq_len, head_dim)
        # K/V use n_kv_heads instead, which equals n_heads unless GQA is on.
        q = q.view(batch, seq_len, self.n_heads, self.head_dim).transpose(1, 2)
        k = k.view(batch, seq_len, self.n_kv_heads, self.head_dim).transpose(1, 2)
        v = v.view(batch, seq_len, self.n_kv_heads, self.head_dim).transpose(1, 2)

        cos, sin = self.rope(seq_len=seq_len, offset=position_offset)
        q = apply_rotary_pos_emb(q, cos, sin)
        k = apply_rotary_pos_emb(k, cos, sin)

        if kv_cache is not None:
            past_k, past_v = kv_cache
            k = torch.cat([past_k, k], dim=2)  # (batch, n_kv_heads, past_len + seq_len, head_dim)
            v = torch.cat([past_v, v], dim=2)

        # The cache stores the UNexpanded K/V -- n_kv_heads, not n_heads. That
        # is the entire point of GQA: cache size scales with n_kv_heads, so
        # halving the K/V heads halves the memory a long generation holds. If
        # the expansion below happened before this line, GQA would still cut
        # parameters but would save nothing at inference time, which is the
        # saving it exists for.
        present_kv = (k, v)

        if self.n_groups > 1:
            # Each K/V head serves n_groups query heads. repeat_interleave (not
            # repeat) is required: query head i must map to K/V head
            # i // n_groups, so the copies of one K/V head have to be adjacent.
            k = k.repeat_interleave(self.n_groups, dim=1)
            v = v.repeat_interleave(self.n_groups, dim=1)

        mask = causal_mask(seq_len_q=seq_len, seq_len_k=k.shape[2], offset=position_offset, device=x.device)

        if self.use_efficient_attention:
            # torch.nn.functional.scaled_dot_product_attention: a fused
            # kernel that never materializes the full (seq_len, seq_len_k)
            # score matrix as a separate tensor the way the manual path
            # below does -- on GPU this dispatches to a FlashAttention-style
            # kernel with O(seq_len) attention-matrix memory instead of
            # O(seq_len^2); on CPU it's mainly a fused-kernel speed/overhead
            # win rather than a big memory one (see README.md's Memory
            # Optimization section for what was actually measured here).
            # Reuses the exact same boolean mask as the manual path, just
            # converted to SDPA's additive-bias convention (0 = attend,
            # -inf = blocked) instead of masked_fill's "True = blocked".
            attn_bias = torch.zeros(mask.shape, dtype=q.dtype, device=x.device).masked_fill(mask, float("-inf"))
            attn_output = F.scaled_dot_product_attention(q, k, v, attn_mask=attn_bias)
        else:
            # Scaled dot-product attention, computed explicitly rather than
            # via the fused kernel above -- slower and O(seq_len^2) memory
            # for the score matrix, but keeps the actual math visible and
            # testable. This is the default, and the reference the
            # efficient path above is checked against (see test_attention.py).
            scale = 1.0 / math.sqrt(self.head_dim)
            attn_scores = (q @ k.transpose(-2, -1)) * scale  # (batch, n_heads, seq_len, seq_len_k)
            attn_scores = attn_scores.masked_fill(mask, float("-inf"))

            # softmax subtracts the row max internally, so this is safe even
            # though attn_scores contains -inf entries (every row keeps at
            # least one unmasked position: a token can always attend to itself).
            attn_weights = F.softmax(attn_scores, dim=-1)
            attn_output = attn_weights @ v  # (batch, n_heads, seq_len, head_dim)

        # (batch, n_heads, seq_len, head_dim) -> (batch, seq_len, n_heads, head_dim) -> (batch, seq_len, d_model)
        attn_output = attn_output.transpose(1, 2).contiguous().view(batch, seq_len, d_model)
        output = self.o_proj(attn_output)

        return output, present_kv
