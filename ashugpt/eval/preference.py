"""Held-out evaluation for a DPO run: loss, ranking accuracy, reward margin.

`perplexity.py` evaluates a cross-entropy model, and its two numbers say
how surprised the model was by text it had to predict. Neither means
anything for a preference objective: the DPO loss is a classification loss
over pairs, and exponentiating it (which is what perplexity is) produces a
number between 1 and 2 that describes nothing.

What a preference run needs held out instead is whether the ranking it
learned generalizes:

- **loss** -- the same objective the run is optimizing, on pairs it has not
  seen. Comparable across runs only at the same `beta`, since beta scales
  the margin inside the loss.
- **accuracy** -- the fraction of held-out pairs whose chosen answer the
  policy already prefers to the rejected one. Starts at 50% (see
  `DPOMetrics.accuracy`) and is the honest headline number.
- **margin** -- the mean implicit-reward gap. Accuracy saturates once the
  ranking is right; the margin keeps moving, which is what makes it the
  useful overfitting signal: a run whose held-out accuracy has plateaued
  while its training margin keeps growing is memorizing the pairs.
- **raw_accuracy** and **length_normalized_accuracy** -- the same ranking
  question asked of the policy alone, with no reference in it. These are the
  only two numbers here that can be compared *across* models, including the
  SFT checkpoint the run started from, and the gap between them is how much
  of the ranking is explained by answer length rather than by content.
- **raw_accuracy_chosen_shorter / raw_accuracy_chosen_longer** -- raw accuracy
  split by which side is longer, which turns that gap from a suspicion into a
  measurement. A model ranking purely by length scores 100% on one of these
  and 0% on the other while looking unremarkable on the average of the two.
- **chosen_reward / rejected_reward** -- the two halves of that margin,
  separately, because the failure mode they diagnose is invisible in the
  difference. A healthy run pushes the chosen reward up; a run that is
  quietly destroying the model pushes *both* down and simply pushes the
  rejected one down faster, which reads as progress in the margin and as
  damage in the samples.

The model must be a `DPOModel`: this reads the per-batch statistics it
stashes on `last_metrics` during its forward pass rather than recomputing
them, so what is reported is exactly what the objective saw.
"""

from __future__ import annotations

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from ashugpt.data.batch import split_batch


@torch.no_grad()
def evaluate_preferences(
    model: nn.Module,
    loader: DataLoader,
    amp_dtype: torch.dtype | None,
    max_batches: int | None = None,
) -> dict[str, float]:
    """Averages DPO metrics over up to `max_batches` batches of `loader`.

    Every average is weighted by the number of *pairs* in the batch, not by
    the number of batches, so a short final batch does not count as much as
    a full one. Leaves the model in eval() mode, like
    `ashugpt.eval.perplexity.evaluate` -- callers resuming training call
    `model.train()` themselves.
    """
    from ashugpt.training.amp import autocast_context

    model.eval()
    device = next(model.parameters()).device

    totals = {
        "loss": 0.0,
        "accuracy": 0.0,
        "raw_accuracy": 0.0,
        "length_normalized_accuracy": 0.0,
        "margin": 0.0,
        "chosen_reward": 0.0,
        "rejected_reward": 0.0,
    }
    n_pairs = 0
    # The length split is counted, not averaged: the two subgroups have
    # different sizes and a per-batch mean would weight them by where the
    # batch boundaries happened to fall.
    shorter_wins = shorter_pairs = longer_wins = longer_pairs = 0.0

    for i, batch in enumerate(loader):
        if max_batches is not None and i >= max_batches:
            break
        input_ids, labels, _ = split_batch(batch, device)
        with autocast_context(device.type, amp_dtype):
            model(input_ids, labels=labels)

        metrics = getattr(model, "last_metrics", None)
        if metrics is None:
            raise TypeError("evaluate_preferences needs a DPOModel: no metrics were recorded by the forward pass")

        pairs = input_ids.shape[0]
        totals["loss"] += metrics.loss.item() * pairs
        totals["accuracy"] += metrics.accuracy.item() * pairs
        totals["raw_accuracy"] += metrics.raw_accuracy.item() * pairs
        totals["length_normalized_accuracy"] += metrics.length_normalized_accuracy.item() * pairs
        totals["margin"] += metrics.margin.item() * pairs
        totals["chosen_reward"] += metrics.chosen_reward.mean().item() * pairs
        totals["rejected_reward"] += metrics.rejected_reward.mean().item() * pairs
        n_pairs += pairs

        raw_wins = (metrics.policy_chosen_logps > metrics.policy_rejected_logps).float()
        chosen_shorter = metrics.chosen_tokens < metrics.rejected_tokens
        chosen_longer = metrics.chosen_tokens > metrics.rejected_tokens
        shorter_wins += raw_wins[chosen_shorter].sum().item()
        shorter_pairs += chosen_shorter.sum().item()
        longer_wins += raw_wins[chosen_longer].sum().item()
        longer_pairs += chosen_longer.sum().item()

    if n_pairs == 0:
        raise ValueError("loader produced no batches to evaluate")

    averaged = {key: value / n_pairs for key, value in totals.items()}
    # nan rather than 0.0 when a subgroup is empty: "no pairs of this kind"
    # and "never got one right" are different statements.
    averaged["raw_accuracy_chosen_shorter"] = shorter_wins / shorter_pairs if shorter_pairs else float("nan")
    averaged["raw_accuracy_chosen_longer"] = longer_wins / longer_pairs if longer_pairs else float("nan")
    averaged["fraction_chosen_shorter"] = shorter_pairs / n_pairs
    return averaged
