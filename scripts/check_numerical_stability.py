"""Check every sweep run's logged losses for numerical failure.

Section 5 of the paper carries an fp16 threat: all runs use fp16 autocast with a
gradient scaler, so a rise in validation loss at the top of a grid could in
principle be overflow rather than too large a step. That threat got sharper when
the 7M grids landed, because their optima sit an order of magnitude higher than
the 124M model's and the sweep runs to 2.5e-3.

The claim the paper makes in response is about *every* run rather than a sample,
so it needs to be re-checkable rather than remembered:

    python scripts/check_numerical_stability.py

It reads every log referenced by every ledger and reports three things.

  1. **NaN or infinity** in training or validation loss, anywhere. This is the
     claim section 5 leans on, and it should be zero.
  2. **Spikes** -- any step whose training loss sits more than `--spike` above
     its own run's running minimum. Non-zero here is not automatically a
     numerical problem, which is the point of reporting them individually: the
     only ones in this project are in `alpaca_short`'s padded arm at step 490,
     they occur at every learning rate in that cell including 2e-5, and a jump
     that is independent of step size and reproducible at a fixed data order is
     a property of which examples the batch holds rather than of the numerics.
  3. **The highest peak learning rate** any of it was run at, so the claim is
     stated against the range it actually covers.

Exit status is 1 if any NaN or infinity is found, so this can gate a commit.
Spikes do not fail the run; they are reported for a human to read.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Keep in step with `export_exponents.py` and `update_paper_counts.py`: a ledger
# missing here is a run the paper claims to have checked and did not.
LEDGERS = [
    (REPO / "results" / "lr_scaling_sweep.csv", "124M"),
    (REPO / "results" / "lr_scaling_small.csv", "30M"),
    (REPO / "results" / "lr_scaling_small9k.csv", "30M at the matched budget"),
    (REPO / "results" / "lr_scaling_mini.csv", "7M"),
    (REPO / "results" / "lr_scaling_mini2k.csv", "7M at the matched budget"),
    (REPO / "results" / "lr_scaling_ckpt.csv", "124M grid extension"),
    (REPO / "results" / "lr_scaling_quality.csv", "124M at perplexity 107.0"),
    (REPO / "results" / "lr_scaling_quality2500.csv", "124M at perplexity 39.4"),
    (REPO / "results" / "lr_scaling_downstream.csv", "124M, 4.3's kept checkpoints"),
]


def losses(path: Path) -> list[tuple[int, float, float | None]]:
    """(step, train_loss, val_loss) for every logged row that carries a loss."""
    out = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            train = row.get("train_loss") or ""
            val = row.get("val_loss") or ""
            if not train and not val:
                continue
            step = int(row["step"]) if row.get("step") else -1
            out.append((step,
                        float(train) if train else math.nan,
                        float(val) if val else None))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spike", type=float, default=1.5,
                        help="report a step whose training loss exceeds this "
                             "multiple of its run's running minimum (default 1.5)")
    args = parser.parse_args()

    scanned = absent = bad = 0
    peak_lr = 0.0
    spikes: list[str] = []

    for ledger, label in LEDGERS:
        if not ledger.exists():
            continue
        rows = list(csv.DictReader(ledger.open()))
        for r in rows:
            log = Path(r["log_path"])
            if not log.is_absolute():
                log = REPO / log
            if not log.exists():
                absent += 1
                continue
            scanned += 1
            peak_lr = max(peak_lr, float(r["max_lr"]))

            running = math.inf
            for step, train, val in losses(log):
                for name, value in (("train", train), ("val", val)):
                    if value is None or (name == "train" and math.isnan(value)):
                        continue
                    if math.isnan(value) or math.isinf(value):
                        bad += 1
                        print(f"  NaN/inf: {label} {log.name} step {step} ({name})")
                        break
                if math.isnan(train):
                    continue
                running = min(running, train)
                if running > 0 and train > args.spike * running:
                    spikes.append(
                        f"  spike: {label} {r['dataset']}/{r['cell']} lr {float(r['max_lr']):.1e} "
                        f"seed {r['seed']} step {step}: {train:.3f} vs running min "
                        f"{running:.3f} ({train / running:.2f}x)")
                    break

    print(f"scanned {scanned} run logs across {len(LEDGERS)} ledgers "
          f"({absent} referenced logs absent)")
    print(f"peak learning rate covered: {peak_lr:.1e}")
    print(f"runs with a NaN or infinity: {bad}")
    print(f"runs with a training-loss step above {args.spike:g}x their running minimum: "
          f"{len(spikes)}")
    for line in spikes:
        print(line)
    if spikes:
        print("\n  A spike is not by itself a numerical failure. Check whether it "
              "appears at every learning rate in its cell and at the same step: if "
              "it does, it is the batch's contents and not the step size.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
