"""RMSNorm: Root Mean Square Layer Normalization.

Mathematical purpose: rescales each token's activation vector so its root-
mean-square is 1, then applies a learned per-dimension scale. Unlike
LayerNorm, it does not re-center the mean -- empirically this works just as
well for transformers while being cheaper (no mean subtraction), and it's
what most modern decoder-only LLMs (LLaMA, Mistral, etc.) use instead of
LayerNorm.

    RMSNorm(x) = (x / sqrt(mean(x^2, dim=-1) + eps)) * weight
"""

from __future__ import annotations

import torch
import torch.nn as nn


class RMSNorm(nn.Module):
    """Input shape: (..., d_model). Output shape: (..., d_model) -- unchanged."""

    def __init__(self, d_model: int, eps: float = 1e-5) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(d_model))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (..., d_model)
        # Compute the mean-square / rsqrt in float32 regardless of the input's
        # dtype -- squaring activations under bf16/fp16 autocast can lose
        # precision or overflow; fp32 keeps this numerically stable.
        input_dtype = x.dtype
        variance = x.float().pow(2).mean(dim=-1, keepdim=True)  # (..., 1)
        normed = x.float() * torch.rsqrt(variance + self.eps)  # (..., d_model)
        return normed.to(input_dtype) * self.weight
