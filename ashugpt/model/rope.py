"""Rotary Positional Embeddings (RoPE).

Mathematical purpose: instead of adding a positional vector to the input
(like GPT-2's learned position embeddings), RoPE *rotates* pairs of
dimensions within each query/key head vector by an angle proportional to
the token's position. The key property this buys us: the dot product
between a rotated query at position m and a rotated key at position n
depends only on the *relative* offset (m - n), not on m and n individually.
That relative-position sensitivity is baked directly into attention scores
without any extra learned parameters, and tends to generalize better to
sequence lengths beyond what was seen during training.

We use the "rotate-half" formulation (as in LLaMA/GPT-NeoX): split each
head vector in half and treat dimension i and dimension i + head_dim/2 as
one rotated pair, rather than interleaving adjacent dimensions. This is
mathematically equivalent to the "interleaved pairs" formulation in the
original RoPE paper -- it's just a different (and more efficient in
PyTorch) way of assigning dimensions to rotation angles.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class RotaryEmbedding(nn.Module):
    """Precomputes cos/sin rotation tables for every position up to max_seq_len.

    forward(seq_len, offset) returns the (cos, sin) slice for positions
    [offset, offset + seq_len) -- the `offset` argument is what makes this
    usable with a KV cache later: a newly generated token isn't at position
    0, it's at position `len(cache so far)`.
    """

    def __init__(self, head_dim: int, max_seq_len: int, theta: float = 10000.0) -> None:
        super().__init__()
        if head_dim % 2 != 0:
            raise ValueError(f"head_dim must be even for RoPE, got {head_dim}")

        # One frequency per dimension-pair: lower dims rotate fast, higher
        # dims rotate slow (theta^(-2i/head_dim) decays as i grows).
        inv_freq = 1.0 / (theta ** (torch.arange(0, head_dim, 2, dtype=torch.float32) / head_dim))  # (head_dim/2,)
        positions = torch.arange(max_seq_len, dtype=torch.float32)  # (max_seq_len,)
        angles = torch.outer(positions, inv_freq)  # (max_seq_len, head_dim/2)

        # Duplicate so each half of the head vector gets a matching angle --
        # dim i and dim i + head_dim/2 rotate together as one pair.
        cos = torch.cat([angles.cos(), angles.cos()], dim=-1)  # (max_seq_len, head_dim)
        sin = torch.cat([angles.sin(), angles.sin()], dim=-1)  # (max_seq_len, head_dim)

        # Non-persistent: these are deterministic from head_dim/theta, not
        # learned, so they shouldn't be saved in checkpoints.
        self.register_buffer("cos_cached", cos, persistent=False)
        self.register_buffer("sin_cached", sin, persistent=False)

    def forward(self, seq_len: int, offset: int = 0) -> tuple[torch.Tensor, torch.Tensor]:
        """Returns (cos, sin), each of shape (seq_len, head_dim)."""
        return (
            self.cos_cached[offset : offset + seq_len],
            self.sin_cached[offset : offset + seq_len],
        )


def rotate_half(x: torch.Tensor) -> torch.Tensor:
    """(..., head_dim) -> (..., head_dim): [-second_half, first_half]."""
    half = x.shape[-1] // 2
    x1, x2 = x[..., :half], x[..., half:]
    return torch.cat([-x2, x1], dim=-1)


def apply_rotary_pos_emb(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """Rotate q or k by the given cos/sin tables.

    x: (..., seq_len, head_dim) -- typically (batch, n_heads, seq_len, head_dim).
    cos, sin: (seq_len, head_dim), broadcast against x's leading dims.
    Output shape == x's shape.

    Equivalent to applying, per dimension-pair (i, i+head_dim/2), the 2D
    rotation matrix [[cos, -sin], [sin, cos]].
    """
    cos = cos.to(dtype=x.dtype)
    sin = sin.to(dtype=x.dtype)
    return x * cos + rotate_half(x) * sin
