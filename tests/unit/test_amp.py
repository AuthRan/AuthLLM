"""Unit tests for mixed-precision helpers."""

import pytest
import torch

from ashugpt.training.amp import autocast_context, build_grad_scaler, resolve_amp_dtype


def test_resolve_amp_dtype() -> None:
    assert resolve_amp_dtype("bfloat16") is torch.bfloat16
    assert resolve_amp_dtype("float16") is torch.float16
    assert resolve_amp_dtype("none") is None


def test_resolve_amp_dtype_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        resolve_amp_dtype("fp8")


def test_autocast_context_applies_requested_dtype() -> None:
    with autocast_context("cpu", torch.bfloat16):
        out = torch.randn(2, 2) @ torch.randn(2, 2)
    assert out.dtype == torch.bfloat16


def test_autocast_context_none_is_a_no_op() -> None:
    with autocast_context("cpu", None):
        out = torch.randn(2, 2) @ torch.randn(2, 2)
    assert out.dtype == torch.float32


def test_grad_scaler_enabled_only_for_float16() -> None:
    assert build_grad_scaler("cpu", torch.float16).is_enabled()
    assert not build_grad_scaler("cpu", torch.bfloat16).is_enabled()
    assert not build_grad_scaler("cpu", None).is_enabled()


def test_disabled_scaler_is_a_transparent_passthrough() -> None:
    # The whole reason to always build a scaler: scale()/step()/update()
    # must be safe to call even when AMP is off or using bf16.
    model = torch.nn.Linear(4, 4)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scaler = build_grad_scaler("cpu", torch.bfloat16)

    with autocast_context("cpu", torch.bfloat16):
        loss = model(torch.randn(3, 4)).sum()

    scaler.scale(loss).backward()
    scaler.unscale_(optimizer)
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    scaler.step(optimizer)
    scaler.update()
    for p in model.parameters():
        assert p.grad is not None
