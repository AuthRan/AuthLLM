"""SwiGLU feed-forward network.

Mathematical purpose: after attention mixes information *across* token
positions, the feed-forward network transforms each token's representation
*independently*, expanding it to a larger hidden dimension and back --
this is where most of a transformer's parameters (and, empirically, a lot
of its capacity) live. SwiGLU is a *gated* variant: one branch ("gate")
learns to modulate, elementwise, how much of another branch ("up") passes
through, before projecting back down:

    SwiGLU(x) = (SiLU(x @ W_gate) * (x @ W_up)) @ W_down

SiLU(z) = z * sigmoid(z) (a smooth, numerically well-behaved activation;
`torch.nn.functional.silu` is used directly rather than reimplementing it).
Three weight matrices instead of a standard FFN's two, so d_ff is
conventionally set smaller than a plain 4x-d_model FFN to keep the
parameter/FLOP budget comparable (see ModelConfig presets' d_ff values).
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class SwiGLU(nn.Module):
    """Input shape: (..., d_model). Output shape: (..., d_model) -- unchanged."""

    def __init__(self, d_model: int, d_ff: int) -> None:
        super().__init__()
        self.gate_proj = nn.Linear(d_model, d_ff, bias=False)
        self.up_proj = nn.Linear(d_model, d_ff, bias=False)
        self.down_proj = nn.Linear(d_ff, d_model, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        gate = F.silu(self.gate_proj(x))  # (..., d_ff)
        up = self.up_proj(x)  # (..., d_ff)
        return self.down_proj(gate * up)  # (..., d_model)
