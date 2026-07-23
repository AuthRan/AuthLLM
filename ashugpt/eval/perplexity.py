"""Held-out evaluation: validation loss and perplexity.

Perplexity is exp(average cross-entropy loss) -- it rescales the loss (in
nats) into "the model's effective branching factor": how many next-token
choices it's effectively as confused between, on average, at each
position. A perfect model has perplexity 1; an untrained model's
perplexity should sit close to vocab_size (uniform-random guessing).
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from ashugpt.training.amp import autocast_context


def perplexity_from_loss(loss: float) -> float:
    return math.exp(loss)


@torch.no_grad()
def evaluate(
    model: nn.Module,
    val_loader: DataLoader,
    amp_dtype: torch.dtype | None,
    max_batches: int | None = None,
) -> dict[str, float]:
    """Runs the validation loop: average cross-entropy loss + perplexity
    over up to `max_batches` batches of val_loader. Leaves the model in
    eval() mode -- callers resuming training should call model.train()
    themselves afterward (kept explicit rather than an automatic side
    effect of this function)."""
    model.eval()
    device = next(model.parameters()).device

    total_loss = 0.0
    num_batches = 0
    for i, (input_ids, labels) in enumerate(val_loader):
        if max_batches is not None and i >= max_batches:
            break
        input_ids, labels = input_ids.to(device), labels.to(device)
        with autocast_context(device.type, amp_dtype):
            output = model(input_ids, labels=labels)
        total_loss += output.loss.item()
        num_batches += 1

    if num_batches == 0:
        raise ValueError("val_loader produced no batches to evaluate")

    avg_loss = total_loss / num_batches
    return {"loss": avg_loss, "perplexity": perplexity_from_loss(avg_loss)}
