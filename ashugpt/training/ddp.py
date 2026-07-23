"""DistributedDataParallel setup/teardown.

torchrun sets RANK, LOCAL_RANK, WORLD_SIZE, MASTER_ADDR, and MASTER_PORT in
the environment of every process it launches. This module reads those,
initializes the process group (gloo on CPU, nccl on GPU -- same code
either way, only the backend name and device differ), and gives the rest
of the training code one small object to make decisions from instead of
scattering `if distributed:` checks and raw env var reads everywhere.

When *not* launched via torchrun (WORLD_SIZE unset or "1"), `setup()`
returns a single-process DistributedInfo without touching
`torch.distributed` at all -- single-GPU/CPU training is just the
world_size=1 case of the same code path, not a separate branch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import torch
import torch.distributed as dist
import torch.nn as nn
from torch.nn.parallel import DistributedDataParallel


@dataclass
class DistributedInfo:
    rank: int
    local_rank: int
    world_size: int
    device: torch.device
    backend: str | None  # None when not actually distributed

    @property
    def is_distributed(self) -> bool:
        return self.world_size > 1

    @property
    def is_main_process(self) -> bool:
        return self.rank == 0


def setup_distributed() -> DistributedInfo:
    world_size = int(os.environ.get("WORLD_SIZE", "1"))

    if world_size == 1:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return DistributedInfo(rank=0, local_rank=0, world_size=1, device=device, backend=None)

    rank = int(os.environ["RANK"])
    local_rank = int(os.environ["LOCAL_RANK"])
    backend = "nccl" if torch.cuda.is_available() else "gloo"

    dist.init_process_group(backend=backend)

    if backend == "nccl":
        torch.cuda.set_device(local_rank)
        device = torch.device(f"cuda:{local_rank}")
    else:
        device = torch.device("cpu")

    return DistributedInfo(rank=rank, local_rank=local_rank, world_size=world_size, device=device, backend=backend)


def cleanup_distributed(info: DistributedInfo) -> None:
    if info.is_distributed:
        dist.destroy_process_group()


def wrap_model_for_ddp(model: nn.Module, info: DistributedInfo) -> nn.Module:
    """Moves model to info.device, then wraps it in DDP if actually
    distributed. DDP itself broadcasts rank 0's initial weights/buffers to
    every other rank as part of construction, so replicas start identical
    even if per-rank random init happened to differ."""
    model = model.to(info.device)
    if not info.is_distributed:
        return model
    device_ids = [info.local_rank] if info.backend == "nccl" else None
    return DistributedDataParallel(model, device_ids=device_ids)


def unwrap_model(model: nn.Module) -> nn.Module:
    """The underlying model, whether or not it's DDP-wrapped. DDP's own
    state_dict() prefixes every key with "module." and does *not* forward
    arbitrary attribute access (e.g. `.config`) to the wrapped model, so
    checkpointing and anything needing model-specific attributes must go
    through this rather than the (possibly DDP-wrapped) model directly --
    that's what keeps a checkpoint saved during distributed training
    loadable by plain single-process inference later."""
    return model.module if isinstance(model, DistributedDataParallel) else model
