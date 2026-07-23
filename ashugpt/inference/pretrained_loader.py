"""Loading GPT-2's public pretrained weights into AshuGPT -- and, more
importantly, correctly determining and reporting that this CANNOT fully
succeed, and precisely why.

GPT-2 was chosen as the comparison target because it's the best-known
decoder-only checkpoint publicly available, and because it differs from
AshuGPT in exactly the dimensions this project actually built (positional
encoding, normalization, FFN structure) -- making the comparison maximally
informative rather than a coincidental near-match.

All facts below about GPT-2 (key names, shapes, config values) were
verified against the real `openai-community/gpt2` checkpoint on Hugging
Face -- its safetensors header (tensor names/shapes/dtypes, fetched via an
HTTP range request, no full download needed) and config.json -- not
recalled from memory. See README.md's Pretrained Checkpoints section for
the exact commands used.

## Architecture comparison: GPT-2 (124M) vs. AshuGPT

| Aspect | GPT-2 | AshuGPT |
|---|---|---|
| Positional encoding | Learned absolute embedding table (`wpe`), added once to input | RoPE: parameter-free rotation of Q/K, applied every layer |
| Normalization | LayerNorm (mean-centered, has bias) | RMSNorm (no centering, no bias) |
| Feed-forward | Plain 2-matrix GELU MLP (`c_fc`, `c_proj`), with bias | Gated 3-matrix SwiGLU (`gate_proj`, `up_proj`, `down_proj`), no bias |
| Attention QKV | One combined `c_attn` matrix (d_model, 3*d_model), `Conv1D` layout (transposed vs. nn.Linear), with bias | Three separate `q_proj`/`k_proj`/`v_proj`, nn.Linear layout, no bias |
| Attention heads | 12 heads, no GQA | Configurable heads, GQA not implemented (n_kv_heads must equal n_heads) -- not a mismatch for GPT-2 specifically, since GPT-2 has no GQA either |
| Vocabulary | 50257, GPT-2's own trained BPE merges | Configurable; AshuGPT's from-scratch BPE (Milestone 2) has independently-trained merges even at the same vocab_size |
| Embedding tying | `lm_head` tied to `wte` (confirmed: no separate `lm_head.weight` in the checkpoint) | Same default (`tie_embeddings=True`) |

## Is direct weight loading possible? No -- two independent, fundamental reasons

1. **Positional encoding.** GPT-2's attention weights were trained assuming
   position information arrives as an additive embedding mixed into the
   input *before* the first layer, and that Q/K are used exactly as
   projected. AshuGPT's attention unconditionally *rotates* Q/K via RoPE
   inside every layer -- a computation GPT-2's weights never saw during
   training and AshuGPT has no flag to disable. There is no tensor to
   place and no rename that fixes this; it's a different algorithm, not a
   differently-named parameter for the same one.
2. **Feed-forward gating.** AshuGPT's SwiGLU FFN has a `gate_proj` matrix
   that GPT-2's plain GELU MLP has no counterpart for *at all*. Loading
   GPT-2's weights would leave `gate_proj` at its random initialization,
   making the result a random-plus-GPT-2 hybrid, not a reproduction of
   GPT-2's actual behavior.

A third, independent issue (even if 1-2 didn't exist): AshuGPT's `RMSNorm`
weight and GPT-2's `LayerNorm` weight have the *same shape* but scale
mathematically different quantities (RMS-normalized vs. mean-centered +
std-normalized) -- shape compatibility is not semantic compatibility.

**What genuinely is a solvable, mechanical conversion problem on its own**
(and is implemented below, correctly, and tested): splitting GPT-2's
combined `c_attn` into separate Q/K/V, undoing `Conv1D`'s transposed
weight layout, and dropping bias terms AshuGPT's bias-free design has no
slot for. If GPT-2 used RoPE and a gated FFN, THAT part alone would be
enough for a working conversion. It doesn't, so it isn't.

## What this module actually does

`convert_gpt2_state_dict()` maps every tensor that *can* be meaningfully
mapped (token embeddings, per-layer attention Q/K/V/O, RMSNorm weights,
final norm) and explicitly reports everything else as `unexpected_keys`
(positional embeddings, every bias, every FFN weight, the static
causal-mask buffer GPT-2 stores as `attn.bias`) rather than silently
dropping it. `load_gpt2_checkpoint()` then **refuses to return a model by
default** (`strict=True` raises `IncompatibleArchitectureError`) --
because "some tensors loaded successfully" is not the same claim as "this
model works," and this loader does not make claims it can't back up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import torch

from ashugpt.config import ModelConfig
from ashugpt.model.gpt import AshuGPT

# Verified live against https://huggingface.co/openai-community/gpt2/resolve/main/config.json
GPT2_CONFIG = {
    "vocab_size": 50257,
    "n_positions": 1024,
    "n_embd": 768,
    "n_layer": 12,
    "n_head": 12,
}

# These apply to *any* GPT-2-family checkpoint (gpt2/-medium/-large/-xl all
# share this architecture, just at different widths/depths) -- not specific
# to the 124M checkpoint verified above.
GPT2_FUNDAMENTAL_INCOMPATIBILITIES = [
    "Positional encoding: GPT-2 adds a learned absolute position embedding "
    "table (wpe, shape (n_positions, n_embd)) to the input once, before the "
    "first block. AshuGPT has no such table -- it injects position "
    "information by rotating Q/K inside every attention layer (RoPE). "
    "GPT-2's attention weights were trained assuming no rotation happens; "
    "AshuGPT's attention unconditionally rotates. No tensor placement or "
    "rename fixes a difference in the actual computation.",
    "Feed-forward network: AshuGPT's SwiGLU FFN has a gate_proj matrix "
    "gating the up-projection via SiLU. GPT-2's MLP is a plain 2-matrix "
    "GELU FFN (c_fc, c_proj) with no gating mechanism. AshuGPT's gate_proj "
    "has no corresponding tensor in a GPT-2 checkpoint -- it would be left "
    "at random initialization, making the result a random-plus-GPT-2 "
    "hybrid, not a reproduction of GPT-2.",
    "Normalization: GPT-2 uses LayerNorm (mean-centered, learnable bias). "
    "AshuGPT uses RMSNorm (no centering, no bias). Their weight tensors "
    "share a shape (n_embd,) but scale mathematically different "
    "quantities -- copying one into the other is not correct just because "
    "the shapes happen to match, and LayerNorm's bias has no destination "
    "in a bias-free RMSNorm at all.",
]


class IncompatibleArchitectureError(RuntimeError):
    """Raised when a source checkpoint's architecture cannot be faithfully
    reproduced by AshuGPT, no matter how tensors are renamed or reshaped."""


@dataclass
class CompatibilityReport:
    missing_keys: list[str] = field(default_factory=list)  # AshuGPT params never populated by the source
    unexpected_keys: list[str] = field(default_factory=list)  # source tensors with nowhere to go
    fundamental_incompatibilities: list[str] = field(default_factory=list)  # reasons no renaming/reshaping fixes it

    @property
    def is_fully_compatible(self) -> bool:
        return not self.fundamental_incompatibilities and not self.missing_keys

    def summary(self) -> str:
        lines: list[str] = []
        if self.fundamental_incompatibilities:
            lines.append("FUNDAMENTAL ARCHITECTURE INCOMPATIBILITIES (no renaming/reshaping fixes these):")
            lines.extend(f"  - {reason}" for reason in self.fundamental_incompatibilities)
        lines.append(f"Missing keys ({len(self.missing_keys)}): AshuGPT parameters with no source tensor")
        lines.extend(f"  - {k}" for k in self.missing_keys[:10])
        if len(self.missing_keys) > 10:
            lines.append(f"  ... and {len(self.missing_keys) - 10} more")
        lines.append(f"Unexpected keys ({len(self.unexpected_keys)}): source tensors with no AshuGPT destination")
        lines.extend(f"  - {k}" for k in self.unexpected_keys[:10])
        if len(self.unexpected_keys) > 10:
            lines.append(f"  ... and {len(self.unexpected_keys) - 10} more")
        return "\n".join(lines)


def gpt2_config_to_model_config(gpt2_config: dict, name: str = "gpt2-shape") -> ModelConfig:
    """A ModelConfig with the same *shape* as a GPT-2 checkpoint -- layers,
    dimensions, heads, vocab, context length all match exactly. This does
    NOT mean the resulting AshuGPT model computes the same function as
    GPT-2 -- see this module's docstring for why."""
    return ModelConfig(
        name=name,
        vocab_size=gpt2_config["vocab_size"],
        d_model=gpt2_config["n_embd"],
        n_layers=gpt2_config["n_layer"],
        n_heads=gpt2_config["n_head"],
        n_kv_heads=gpt2_config["n_head"],  # GPT-2 has no GQA -- matches AshuGPT's only supported case
        d_ff=4 * gpt2_config["n_embd"],  # GPT-2's mlp.c_fc width -- structurally analogous, not computationally equivalent
        context_length=gpt2_config["n_positions"],
        tie_embeddings=True,  # confirmed: no separate lm_head.weight in the real checkpoint
    )


def convert_gpt2_state_dict(
    source: dict[str, torch.Tensor], config: ModelConfig
) -> tuple[dict[str, torch.Tensor], CompatibilityReport]:
    """Maps whatever CAN be meaningfully mapped from a GPT-2 (HF-convention)
    state dict onto AshuGPT's parameter names. Returns the converted dict
    plus a report whose unexpected_keys and fundamental_incompatibilities
    are already filled in (missing_keys gets filled in by
    load_gpt2_checkpoint, once an actual model exists to ask)."""
    converted: dict[str, torch.Tensor] = {}
    consumed: set[str] = set()

    def take(key: str) -> torch.Tensor | None:
        if key in source:
            consumed.add(key)
            return source[key]
        return None

    # Token embeddings: shape-comparable ((vocab_size, d_model)) -- but note
    # this does NOT mean the embedding *content* is meaningful without also
    # adopting GPT-2's exact tokenizer. Row i means a different token in
    # AshuGPT's independently-trained BPE vocabulary than in GPT-2's.
    wte = take("wte.weight")
    if wte is not None:
        converted["token_embedding.weight"] = wte
        if config.tie_embeddings:
            converted["lm_head.weight"] = wte

    ln_f_weight = take("ln_f.weight")
    if ln_f_weight is not None:
        converted["final_norm.weight"] = ln_f_weight

    for i in range(config.n_layers):
        prefix = f"h.{i}."

        ln1_w = take(prefix + "ln_1.weight")
        if ln1_w is not None:
            converted[f"blocks.{i}.attn_norm.weight"] = ln1_w

        # c_attn: (d_model, 3*d_model) in Conv1D's (in, out) layout.
        # Transpose to nn.Linear's (out, in), then split the *output*
        # dimension into three equal Q/K/V chunks -- GPT-2 concatenates
        # them in that order (verified: HF's GPT2Attention splits c_attn's
        # output into query/key/value in exactly that sequence).
        c_attn_w = take(prefix + "attn.c_attn.weight")
        if c_attn_w is not None:
            q_w, k_w, v_w = c_attn_w.t().chunk(3, dim=0)
            converted[f"blocks.{i}.attn.q_proj.weight"] = q_w
            converted[f"blocks.{i}.attn.k_proj.weight"] = k_w
            converted[f"blocks.{i}.attn.v_proj.weight"] = v_w

        c_proj_w = take(prefix + "attn.c_proj.weight")
        if c_proj_w is not None:
            converted[f"blocks.{i}.attn.o_proj.weight"] = c_proj_w.t()

        ln2_w = take(prefix + "ln_2.weight")
        if ln2_w is not None:
            converted[f"blocks.{i}.ffn_norm.weight"] = ln2_w

        # Deliberately NOT mapped: mlp.c_fc / mlp.c_proj (no SwiGLU shape
        # to receive a plain 2-matrix MLP into; gate_proj has no source at
        # all), every *.bias (AshuGPT has none), wpe.weight (no RoPE
        # equivalent), and h.{i}.attn.bias (a static causal-mask buffer in
        # the source, not a trainable weight, and AshuGPT computes its
        # causal mask on the fly instead of storing one). All of these
        # simply never get `take()`-en, so they surface as unexpected_keys
        # below rather than being silently discarded without a trace.

    unexpected = sorted(k for k in source if k not in consumed)
    report = CompatibilityReport(
        unexpected_keys=unexpected,
        fundamental_incompatibilities=list(GPT2_FUNDAMENTAL_INCOMPATIBILITIES),
    )
    return converted, report


def load_gpt2_checkpoint(
    source: dict[str, torch.Tensor],
    gpt2_config: dict | None = None,
    strict: bool = True,
) -> tuple[AshuGPT, CompatibilityReport]:
    """Attempts to load a GPT-2 (HF-convention) state dict into a freshly
    constructed, shape-matched AshuGPT model.

    strict=True (default): raises IncompatibleArchitectureError instead of
    returning a model. GPT-2 IS fundamentally incompatible with AshuGPT's
    architecture (see module docstring) -- this is the required "fail
    loudly on incompatible weights" behavior, not an overly-cautious default.

    strict=False: returns the model anyway, for inspection only. Its
    outputs are not meaningful -- gate_proj and RoPE's implicit
    assumptions were never satisfied by anything in `source`.
    """
    gpt2_config = gpt2_config or GPT2_CONFIG
    config = gpt2_config_to_model_config(gpt2_config)
    model = AshuGPT(config)

    converted, report = convert_gpt2_state_dict(source, config)
    load_result = model.load_state_dict(converted, strict=False)
    # PyTorch's own accounting, from the real model -- authoritative,
    # supersedes any prediction convert_gpt2_state_dict could make on its own.
    report.missing_keys = sorted(load_result.missing_keys)

    if strict and not report.is_fully_compatible:
        raise IncompatibleArchitectureError(
            "GPT-2's checkpoint cannot be faithfully loaded into AshuGPT -- "
            "the architectures are fundamentally different, not just "
            "differently named (see ashugpt/inference/pretrained_loader.py's "
            "module docstring). Pass strict=False to load the partial, "
            "NON-FUNCTIONAL result anyway (inspection only), or use GPT-2's "
            "own architecture (e.g. transformers.GPT2LMHeadModel) to "
            "actually run this checkpoint.\n\n" + report.summary()
        )

    return model, report
