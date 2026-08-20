"""A single decoder Transformer block: pre-norm attention + pre-norm FFN,
each wrapped in a residual connection.

    x = x + Attention(RMSNorm(x))
    x = x + SwiGLU(RMSNorm(x))

"Pre-norm" (normalize *before* the sub-layer, not after) is what modern
LLMs (LLaMA, GPT-NeoX, etc.) use instead of the original Transformer's
post-norm -- it keeps gradients better behaved through many stacked blocks.
The residual ("+x") connections are what let gradients flow directly back
through arbitrarily many blocks without vanishing.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from ashugpt.model.attention import CausalSelfAttention
from ashugpt.model.feedforward import SwiGLU
from ashugpt.model.norm import RMSNorm


class TransformerBlock(nn.Module):
    """Input shape: (batch, seq_len, d_model). Output shape: (batch, seq_len, d_model)."""

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        n_kv_heads: int,
        d_ff: int,
        max_seq_len: int,
        rope_theta: float = 10000.0,
        norm_eps: float = 1e-5,
    ) -> None:
        super().__init__()
        self.attn_norm = RMSNorm(d_model, eps=norm_eps)
        self.attn = CausalSelfAttention(d_model, n_heads, n_kv_heads, max_seq_len, rope_theta)
        self.ffn_norm = RMSNorm(d_model, eps=norm_eps)
        self.ffn = SwiGLU(d_model, d_ff)

    def forward(
        self,
        x: torch.Tensor,
        kv_cache: tuple[torch.Tensor, torch.Tensor] | None = None,
        position_offset: int = 0,
        segment_ids: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, tuple[torch.Tensor, torch.Tensor]]:
        """See CausalSelfAttention.forward for every argument's semantics."""
        attn_out, present_kv = self.attn(
            self.attn_norm(x),
            kv_cache=kv_cache,
            position_offset=position_offset,
            segment_ids=segment_ids,
            position_ids=position_ids,
        )
        x = x + attn_out
        x = x + self.ffn(self.ffn_norm(x))
        return x, present_kv
