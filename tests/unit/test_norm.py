"""Unit tests for RMSNorm."""

import torch

from ashugpt.model.norm import RMSNorm


def test_output_shape_matches_input() -> None:
    norm = RMSNorm(d_model=16)
    x = torch.randn(3, 5, 16)
    assert norm(x).shape == x.shape


def test_default_weight_produces_unit_rms() -> None:
    # weight starts at all-ones, so the output's root-mean-square along the
    # last dim should be ~1 for any nonzero input.
    norm = RMSNorm(d_model=32, eps=1e-8)
    x = torch.randn(4, 7, 32) * 10.0  # arbitrary scale
    out = norm(x)
    rms = out.pow(2).mean(dim=-1).sqrt()
    assert torch.allclose(rms, torch.ones_like(rms), atol=1e-3)


def test_weight_scales_output_elementwise() -> None:
    norm = RMSNorm(d_model=4, eps=1e-8)
    with torch.no_grad():
        norm.weight.copy_(torch.tensor([1.0, 2.0, 3.0, 4.0]))
    x = torch.randn(2, 4)

    out = norm(x)
    variance = x.pow(2).mean(dim=-1, keepdim=True)
    expected = (x * torch.rsqrt(variance + norm.eps)) * torch.tensor([1.0, 2.0, 3.0, 4.0])
    assert torch.allclose(out, expected, atol=1e-5)


def test_zero_input_is_finite_not_nan() -> None:
    norm = RMSNorm(d_model=8)
    x = torch.zeros(2, 8)
    out = norm(x)
    assert torch.isfinite(out).all()
    assert torch.equal(out, torch.zeros_like(out))


def test_large_magnitude_input_stays_finite() -> None:
    norm = RMSNorm(d_model=8)
    x = torch.randn(2, 8) * 1e6
    out = norm(x)
    assert torch.isfinite(out).all()


def test_normalizes_each_position_independently() -> None:
    norm = RMSNorm(d_model=4, eps=1e-8)
    x = torch.randn(1, 3, 4)
    out_full = norm(x)

    # Normalizing position 1 alone must give the same result as it got as
    # part of the batch -- confirms no cross-position leakage.
    out_single = norm(x[:, 1:2, :])
    assert torch.allclose(out_full[:, 1:2, :], out_single, atol=1e-6)
