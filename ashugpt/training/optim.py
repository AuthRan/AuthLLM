"""Optimizer and learning-rate schedule for pretraining."""

from __future__ import annotations

import math

import torch
import torch.nn as nn

from ashugpt.config import TrainConfig


def build_optimizer(
    model: nn.Module,
    lr: float,
    weight_decay: float,
    betas: tuple[float, float] = (0.9, 0.95),
    optimizer: str = "adamw",
) -> torch.optim.Optimizer:
    """Builds either AdamW (default) or SGD+momentum, with weight decay
    applied only to 2D+ weight matrices either way.

    Excludes 1D parameters (RMSNorm scale weights are the only ones in this
    architecture, since every Linear is bias-free) from decay -- a
    well-known pretraining detail: decaying a normalization scale toward
    zero tends to hurt rather than help, unlike a weight matrix.

    optimizer="sgd" is the memory-efficient alternative (Milestone 9): it
    keeps one momentum buffer per parameter instead of AdamW's two (exp_avg
    + exp_avg_sq), at the cost of typically needing more steps/careful LR
    tuning to converge as well -- see README.md's Memory Optimization
    section for the measured difference and the tradeoff in full.
    """
    decay_params = [p for p in model.parameters() if p.requires_grad and p.dim() >= 2]
    no_decay_params = [p for p in model.parameters() if p.requires_grad and p.dim() < 2]
    param_groups = [
        {"params": decay_params, "weight_decay": weight_decay},
        {"params": no_decay_params, "weight_decay": 0.0},
    ]
    if optimizer == "adamw":
        return torch.optim.AdamW(param_groups, lr=lr, betas=betas)
    if optimizer == "sgd":
        return torch.optim.SGD(param_groups, lr=lr, momentum=betas[0])
    raise ValueError(f"optimizer must be 'adamw' or 'sgd', got '{optimizer}'")


def get_lr(step: int, config: TrainConfig) -> float:
    """Linear warmup from ~0 to max_lr over `warmup_steps`, then cosine
    decay from max_lr down to min_lr over the remaining steps until
    `max_steps`; holds at min_lr afterward."""
    if step < config.warmup_steps:
        return config.max_lr * (step + 1) / config.warmup_steps
    if step >= config.max_steps:
        return config.min_lr
    decay_ratio = (step - config.warmup_steps) / max(1, config.max_steps - config.warmup_steps)
    coeff = 0.5 * (1.0 + math.cos(math.pi * decay_ratio))  # 1 -> 0 across the decay window
    return config.min_lr + coeff * (config.max_lr - config.min_lr)
