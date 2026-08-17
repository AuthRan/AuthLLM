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


def warn_if_amp_dtype_is_slow(device_type: str, amp_dtype: torch.dtype | None) -> str | None:
    """Return a warning string if the chosen AMP dtype is a bad fit for the
    hardware it is about to run on, else None.

    This exists because of a specific, measured trap. `torch.cuda.is_bf16_supported()`
    returns True on Turing (sm_75) cards like the RTX 2080 Ti, because recent
    PyTorch counts *emulated* bf16 as supported. Emulated is not free.
    Measured on a 2080 Ti, 4096x4096 matmul:

        fp16   57.3 TFLOP/s
        fp32   12.6 TFLOP/s
        bf16    7.7 TFLOP/s     <- 7.4x slower than fp16, and slower than fp32

    So a config that says `amp_dtype: bfloat16` -- which every preset in this
    repo did by default, since bf16 is the right answer on CPU and on Ampere+
    -- silently runs several times slower than fp32 on this hardware, while
    looking like it enabled an optimization. That is the same lesson as
    README.md section 8.1's CPU bf16 finding, in a form that is harder to
    notice because the "is it supported?" check says yes.

    Native bf16 tensor cores start at compute capability 8.0 (Ampere).
    """
    if amp_dtype is not torch.bfloat16 or device_type != "cuda":
        return None
    if not torch.cuda.is_available():
        return None

    major, _ = torch.cuda.get_device_capability()
    if major >= 8:
        return None

    name = torch.cuda.get_device_name()
    return (
        f"amp_dtype='bfloat16' on {name} (compute capability {major}.x): this GPU has no native bf16, "
        f"so bf16 is emulated and measurably slower than both fp16 and fp32 here. "
        f"Use amp_dtype='float16' (with the GradScaler this module already builds) instead. "
        f"Note torch.cuda.is_bf16_supported() returns True anyway -- it counts emulation."
    )


def autocast_context(device_type: str, amp_dtype: torch.dtype | None):
    """A real autocast context if amp_dtype is set, otherwise a no-op one."""
    return torch.autocast(device_type=device_type, dtype=amp_dtype or torch.float32, enabled=amp_dtype is not None)


def build_grad_scaler(device_type: str, amp_dtype: torch.dtype | None) -> torch.amp.GradScaler:
    return torch.amp.GradScaler(device=device_type, enabled=amp_dtype is torch.float16)
