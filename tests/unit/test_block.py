"""Unit tests for TransformerBlock (composition of norm + attention + FFN)."""

import torch

from ashugpt.model.block import TransformerBlock


def make_block(d_model: int = 32, n_heads: int = 4, d_ff: int = 48, max_seq_len: int = 16) -> TransformerBlock:
    torch.manual_seed(0)
    return TransformerBlock(d_model=d_model, n_heads=n_heads, n_kv_heads=n_heads, d_ff=d_ff, max_seq_len=max_seq_len)


def test_output_shape_matches_input() -> None:
    block = make_block(d_model=32, n_heads=4, d_ff=48)
    x = torch.randn(2, 10, 32)
    out, (k, v) = block(x)
    assert out.shape == x.shape
    assert k.shape == (2, 4, 10, 8)


def test_gradients_flow_through_the_whole_block() -> None:
    block = make_block()
    x = torch.randn(2, 6, 32, requires_grad=True)
    out, _ = block(x)
    out.sum().backward()

    assert x.grad is not None and torch.isfinite(x.grad).all()
    for name, param in block.named_parameters():
        assert param.grad is not None, f"{name} got no gradient"
        assert torch.isfinite(param.grad).all(), f"{name} got a non-finite gradient"


def test_block_cannot_attend_to_future_tokens() -> None:
    block = make_block(d_model=32, n_heads=4)
    block.eval()

    torch.manual_seed(1)
    x = torch.randn(2, 10, 32)
    x_modified = x.clone()
    x_modified[:, 5:, :] = torch.randn_like(x_modified[:, 5:, :])

    with torch.no_grad():
        out_original, _ = block(x)
        out_modified, _ = block(x_modified)

    assert torch.allclose(out_original[:, :5, :], out_modified[:, :5, :], atol=1e-6)
    assert not torch.allclose(out_original[:, 5:, :], out_modified[:, 5:, :], atol=1e-6)


def test_incremental_forward_matches_full_forward() -> None:
    block = make_block(d_model=16, n_heads=2, d_ff=32, max_seq_len=16)
    block.eval()

    torch.manual_seed(2)
    x = torch.randn(1, 8, 16)

    with torch.no_grad():
        out_full, _ = block(x, kv_cache=None, position_offset=0)
        out_first, kv_first = block(x[:, :5, :], kv_cache=None, position_offset=0)
        out_second, _ = block(x[:, 5:, :], kv_cache=kv_first, position_offset=5)

    assert torch.allclose(out_full[:, :5, :], out_first, atol=1e-5)
    assert torch.allclose(out_full[:, 5:, :], out_second, atol=1e-5)
