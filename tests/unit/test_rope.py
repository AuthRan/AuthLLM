"""Unit tests for RoPE."""

import pytest
import torch

from ashugpt.model.rope import RotaryEmbedding, apply_rotary_pos_emb


def test_rejects_odd_head_dim() -> None:
    with pytest.raises(ValueError):
        RotaryEmbedding(head_dim=7, max_seq_len=10)


def test_cos_sin_shapes() -> None:
    rope = RotaryEmbedding(head_dim=8, max_seq_len=20)
    cos, sin = rope(seq_len=5, offset=0)
    assert cos.shape == (5, 8)
    assert sin.shape == (5, 8)


def test_position_zero_is_identity_rotation() -> None:
    # angle = 0 * freq = 0, so cos=1, sin=0 -> rotation should be a no-op.
    rope = RotaryEmbedding(head_dim=8, max_seq_len=10)
    cos, sin = rope(seq_len=1, offset=0)
    x = torch.randn(1, 1, 1, 8)
    rotated = apply_rotary_pos_emb(x, cos, sin)
    assert torch.allclose(rotated, x, atol=1e-6)


def test_output_shape_matches_input() -> None:
    rope = RotaryEmbedding(head_dim=16, max_seq_len=20)
    cos, sin = rope(seq_len=6, offset=0)
    x = torch.randn(2, 4, 6, 16)  # (batch, n_heads, seq_len, head_dim)
    rotated = apply_rotary_pos_emb(x, cos, sin)
    assert rotated.shape == x.shape


def test_rotation_preserves_vector_norm() -> None:
    # RoPE is a rotation -- an orthogonal transform -- so it must not change
    # the length of the vector it's applied to.
    rope = RotaryEmbedding(head_dim=8, max_seq_len=20)
    cos, sin = rope(seq_len=4, offset=0)
    x = torch.randn(1, 1, 4, 8)
    rotated = apply_rotary_pos_emb(x, cos, sin)
    assert torch.allclose(x.norm(dim=-1), rotated.norm(dim=-1), atol=1e-5)


def test_offset_slices_are_continuous_with_a_single_call() -> None:
    # This is exactly what a future KV cache relies on: computing rope for
    # tokens [0,5) and then separately for [5,8) must match computing it for
    # [0,8) in one call, so continuing generation from a cache is consistent
    # with having processed the whole sequence at once.
    rope = RotaryEmbedding(head_dim=8, max_seq_len=20)
    cos_first, sin_first = rope(seq_len=5, offset=0)
    cos_second, sin_second = rope(seq_len=3, offset=5)
    cos_full, sin_full = rope(seq_len=8, offset=0)

    assert torch.equal(torch.cat([cos_first, cos_second]), cos_full)
    assert torch.equal(torch.cat([sin_first, sin_second]), sin_full)


def test_attention_score_depends_only_on_relative_position() -> None:
    # The defining property of RoPE: dot(rotate(q, m), rotate(k, n)) depends
    # only on (m - n), not on the absolute positions m and n.
    head_dim = 8
    rope = RotaryEmbedding(head_dim=head_dim, max_seq_len=50)
    torch.manual_seed(0)
    q_content = torch.randn(1, 1, 1, head_dim)
    k_content = torch.randn(1, 1, 1, head_dim)

    def rotated_dot(q_pos: int, k_pos: int) -> torch.Tensor:
        cos_q, sin_q = rope(seq_len=1, offset=q_pos)
        cos_k, sin_k = rope(seq_len=1, offset=k_pos)
        q_rot = apply_rotary_pos_emb(q_content, cos_q, sin_q)
        k_rot = apply_rotary_pos_emb(k_content, cos_k, sin_k)
        return (q_rot * k_rot).sum()

    # Two different absolute position pairs, same relative offset (3).
    score_a = rotated_dot(q_pos=5, k_pos=2)
    score_b = rotated_dot(q_pos=40, k_pos=37)
    assert torch.allclose(score_a, score_b, atol=1e-4)

    # A different relative offset should (generically) give a different score.
    score_c = rotated_dot(q_pos=5, k_pos=1)
    assert not torch.allclose(score_a, score_c, atol=1e-4)
