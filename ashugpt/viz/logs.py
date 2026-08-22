"""Reading the CSV a training run wrote.

Every trainer in this repo logs the same five columns -- step, train_loss,
lr, val_loss, val_perplexity -- with the train row and the eval row written
separately, so a step that was evaluated appears twice: once with
train_loss and no val_loss, once with val_loss and no train_loss. That
shape is why this module exists rather than a bare csv.reader at each call
site: every plot needs the two series pulled apart the same way.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


def _to_float(value: str) -> float | None:
    """CSV writes an absent metric as the empty string, not as a sentinel."""
    value = value.strip()
    if not value:
        return None
    return float(value)


@dataclass
class TrainingLog:
    """One run's curves, with the train and eval series already separated."""

    name: str
    path: Path
    train_steps: list[int] = field(default_factory=list)
    train_loss: list[float] = field(default_factory=list)
    lr: list[float] = field(default_factory=list)
    val_steps: list[int] = field(default_factory=list)
    val_loss: list[float] = field(default_factory=list)
    val_perplexity: list[float] = field(default_factory=list)

    @property
    def final_val_loss(self) -> float | None:
        return self.val_loss[-1] if self.val_loss else None

    @property
    def max_step(self) -> int:
        return max(self.train_steps) if self.train_steps else 0

    def smoothed_train_loss(self, window: int = 1) -> list[float]:
        """Trailing mean over `window` points.

        A per-step training loss on a batch of 4-32 examples is noisy enough
        that two runs' curves can be told apart only by their trend, so the
        comparison plots smooth. `window=1` returns the raw series, and the
        raw series is what the single-run plots draw underneath.
        """
        if window <= 1:
            return list(self.train_loss)
        out: list[float] = []
        acc: list[float] = []
        for value in self.train_loss:
            acc.append(value)
            if len(acc) > window:
                acc.pop(0)
            out.append(sum(acc) / len(acc))
        return out


def load_log(path: str | Path, name: str | None = None) -> TrainingLog:
    """Parse one trainer CSV into a TrainingLog.

    Rows carrying train_loss and rows carrying val_loss are collected into
    separate series, because a run evaluates every eval_interval steps and
    the two therefore have different lengths and different x-values.
    """
    path = Path(path)
    log = TrainingLog(name=name or path.stem, path=path)
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            step = int(row["step"])
            train = _to_float(row.get("train_loss", ""))
            if train is not None:
                log.train_steps.append(step)
                log.train_loss.append(train)
                lr = _to_float(row.get("lr", ""))
                log.lr.append(lr if lr is not None else float("nan"))
            val = _to_float(row.get("val_loss", ""))
            if val is not None:
                log.val_steps.append(step)
                log.val_loss.append(val)
                ppl = _to_float(row.get("val_perplexity", ""))
                if ppl is not None:
                    log.val_perplexity.append(ppl)
    return log
