"""One place that knows how a batch is shaped.

Unpacked datasets yield (input_ids, labels). The packed instruction dataset
yields two more tensors -- segment_ids and position_ids -- which the model
takes as keyword arguments. Rather than teach the trainer and the evaluation
loop each to recognize both shapes, both call `split_batch`, which returns the
two positional tensors plus a dict to splat into the model.

Keeping this in the data package, not the training one, is deliberate:
ashugpt.eval.perplexity already imports from ashugpt.training lazily to dodge
a circular import, and this helper would otherwise deepen that knot.
"""

from __future__ import annotations

import torch

# Order must match PackedInstructionDataset.__getitem__'s return.
_EXTRA_FIELDS = ("segment_ids", "position_ids")


def split_batch(
    batch: tuple[torch.Tensor, ...],
    device: torch.device | str,
    non_blocking: bool = False,
) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
    """Move a batch to `device` and split it into (input_ids, labels, extras).

    `extras` is empty for the usual two-tensor batch, so callers can always
    write `model(input_ids, labels=labels, **extras)` without branching.
    """
    if len(batch) < 2:
        raise ValueError(f"a batch must hold at least input_ids and labels, got {len(batch)} tensors")
    if len(batch) > 2 + len(_EXTRA_FIELDS):
        raise ValueError(f"unexpected batch of {len(batch)} tensors; known extras are {_EXTRA_FIELDS}")

    moved = [t.to(device, non_blocking=non_blocking) for t in batch]
    input_ids, labels = moved[0], moved[1]
    extras = dict(zip(_EXTRA_FIELDS, moved[2:]))
    return input_ids, labels, extras
