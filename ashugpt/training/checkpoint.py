"""Checkpoint save/resume for training.

A *resumable training* checkpoint has to bundle heterogeneous state --
model weights, optimizer momentum buffers, and a step counter -- which
doesn't fit a pure-tensor format like safetensors (that's a better fit for
weights-only distribution, e.g. a future inference-only export). We use
torch.save/torch.load instead, with `weights_only=True` on load: that still
restricts unpickling to plain tensors/dicts/numbers, not arbitrary code
execution, which is all a checkpoint we wrote ourselves ever needs -- the
config dataclasses are converted via dataclasses.asdict() first, since a
raw dataclass instance isn't on the weights_only allowlist.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import torch
import torch.nn as nn

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.model.gpt import AshuGPT


def save_checkpoint(
    path: str | Path,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    step: int,
    model_config: ModelConfig,
    train_config: TrainConfig,
) -> None:
    checkpoint = {
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "step": step,
        "model_config": asdict(model_config),
        "train_config": asdict(train_config),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, path)


def load_checkpoint(
    path: str | Path,
    model: nn.Module,
    optimizer: torch.optim.Optimizer | None = None,
    map_location: str = "cpu",
) -> int:
    """Loads model weights (and optimizer state, if given) in place.
    Returns the step training should resume from."""
    checkpoint = torch.load(Path(path), map_location=map_location, weights_only=True)
    model.load_state_dict(checkpoint["model_state_dict"])
    if optimizer is not None:
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
    return checkpoint["step"]


def load_model_for_inference(path: str | Path, map_location: str = "cpu") -> nn.Module:
    """Reconstructs an AshuGPT model purely from a checkpoint file (using
    its saved model_config) and loads its weights -- for generation/
    inference call sites that don't already have a model instance built,
    unlike load_checkpoint() above which loads into one you provide."""
    checkpoint = torch.load(Path(path), map_location=map_location, weights_only=True)
    model_config = ModelConfig(**checkpoint["model_config"])
    model = AshuGPT(model_config)
    model.load_state_dict(checkpoint["model_state_dict"])
    return model
