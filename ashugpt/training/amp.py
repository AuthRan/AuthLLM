"""Mixed-precision training helpers.

Uses torch.amp's unified API, which works the same way on CPU and CUDA.
bfloat16 autocast needs no gradient scaling (bf16 has the same exponent
range as fp32, just less mantissa precision -- it can't overflow/underflow
the way fp16 can). float16 has a much narrower exponent range, so small
gradients can silently underflow to zero without loss scaling.

We always build a GradScaler, but leave it *disabled* unless amp_dtype is
float16 -- a disabled GradScaler's scale/unscale_/step/update calls are all
no-ops, so the training loop can call them unconditionally regardless of
which precision (or none) is configured, rather than branching on it.
"""

from __future__ import annotations

import torch

_AMP_DTYPES: dict[str, torch.dtype | None] = {
    "bfloat16": torch.bfloat16,
    "float16": torch.float16,
    "none": None,
}


def resolve_amp_dtype(name: str) -> torch.dtype | None:
    try:
        return _AMP_DTYPES[name]
    except KeyError:
        raise ValueError(f"Unknown amp_dtype '{name}', expected one of {list(_AMP_DTYPES)}") from None


def autocast_context(device_type: str, amp_dtype: torch.dtype | None):
    """A real autocast context if amp_dtype is set, otherwise a no-op one."""
    return torch.autocast(device_type=device_type, dtype=amp_dtype or torch.float32, enabled=amp_dtype is not None)


def build_grad_scaler(device_type: str, amp_dtype: torch.dtype | None) -> torch.amp.GradScaler:
    return torch.amp.GradScaler(device=device_type, enabled=amp_dtype is torch.float16)
