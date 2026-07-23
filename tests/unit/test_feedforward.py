"""Unit tests for the SwiGLU feed-forward network."""

import torch
import torch.nn.functional as F

from ashugpt.model.feedforward import SwiGLU


def test_output_shape_matches_input() -> None:
    ffn = SwiGLU(d_model=16, d_ff=48)
    x = torch.randn(3, 5, 16)
    assert ffn(x).shape == x.shape


def test_zero_input_gives_zero_output() -> None:
    # SiLU(0) = 0 * sigmoid(0) = 0, so gate is always 0 for zero input,
    # zeroing the gated product regardless of the up/down weights (and all
    # projections are bias-free, so there's no bias term to leak through).
    ffn = SwiGLU(d_model=8, d_ff=16)
    x = torch.zeros(2, 8)
    out = ffn(x)
    assert torch.equal(out, torch.zeros_like(out))


def test_matches_manual_computation_with_known_weights() -> None:
    torch.manual_seed(0)
    ffn = SwiGLU(d_model=3, d_ff=4)
    x = torch.randn(2, 3)

    with torch.no_grad():
        expected_gate = F.silu(x @ ffn.gate_proj.weight.T)
        expected_up = x @ ffn.up_proj.weight.T
        expected = (expected_gate * expected_up) @ ffn.down_proj.weight.T

    out = ffn(x)
    assert torch.allclose(out, expected, atol=1e-5)


def test_gradients_flow_to_all_projections() -> None:
    ffn = SwiGLU(d_model=8, d_ff=16)
    x = torch.randn(4, 8, requires_grad=True)
    out = ffn(x)
    out.sum().backward()

    assert x.grad is not None
    assert torch.isfinite(x.grad).all()
    for param in ffn.parameters():
        assert param.grad is not None
        assert torch.isfinite(param.grad).all()
