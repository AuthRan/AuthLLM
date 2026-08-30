"""Split each matched-budget exponent into its batch term and its step term.

Section 4.6 reports this decomposition in a table and section 4.3 explains it:
at a fixed data budget the exponent is a batch term plus a step term, because
packing changes both the batch and the step count and the two effects add in
logs. The batch term is the comparison at a *matched step count* -- packed
against padded, both at the short cell -- and the step term is the padded arm
compared against itself at the two step counts.

Those numbers were computed by hand and typed into the paper, and when a seed
replication moved an optimum they did not move with it. This derives them from
the same ledger and the same estimator as everything else, so they cannot drift.

    python scripts/export_decomposition.py            # -> results/decomposition.csv

Columns: model, dataset, exponent, batch_term, step_term, residual, seeds.
`residual` is exponent - (batch + step) and is a rounding check on the identity,
not a measured quantity: it should be zero to within floating point.
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_lr_scaling as A  # noqa: E402
from analyze_lr_scaling import STEP_COUNTS, SUPERVISED_TOKENS, cell_name, cell_optimum, load  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "results" / "decomposition.csv"

# Same list as export_exponents.py; keep the two in step.
LEDGERS = [
    ("124M", REPO / "results" / "lr_scaling_sweep.csv"),
    ("30M", REPO / "results" / "lr_scaling_small.csv"),
    ("7M", REPO / "results" / "lr_scaling_mini.csv"),
    ("30M@19.7", REPO / "results" / "lr_scaling_small9k.csv"),
    ("7M@18.0", REPO / "results" / "lr_scaling_mini2k.csv"),
    ("124M@ppl107", REPO / "results" / "lr_scaling_quality.csv"),
    ("124M@ppl39", REPO / "results" / "lr_scaling_quality2500.csv"),
]

FIELDS = ["model", "dataset", "exponent", "batch_term", "step_term", "residual", "seeds"]


def rows() -> list[dict]:
    out = []
    for model, path in LEDGERS:
        if not path.exists():
            continue
        A.RESULTS = path
        for dataset, (short, long) in STEP_COUNTS.items():
            curves = load("final_val_loss", dataset)
            packed_short = cell_name(True, short)
            padded_short = cell_name(False, short)
            padded_long = cell_name(False, long)
            if not all(c in curves for c in (packed_short, padded_short, padded_long)):
                continue
            solved = {}
            for cell in (packed_short, padded_short, padded_long):
                lr, _spread, ok, seeds = cell_optimum(curves[cell])
                if not ok:
                    break
                solved[cell] = (lr, seeds)
            if len(solved) != 3:
                continue

            tokens = SUPERVISED_TOKENS[dataset]
            factor = tokens[True] / tokens[False]
            log_p = math.log(factor)
            # Batch: packed against padded at the same step count.
            batch = math.log(solved[packed_short][0] / solved[padded_short][0]) / log_p
            # Step: the padded arm at the short step count against the long one.
            step = math.log(solved[padded_short][0] / solved[padded_long][0]) / log_p
            # The headline: packed short against padded long, one epoch each.
            total = math.log(solved[packed_short][0] / solved[padded_long][0]) / log_p
            out.append({
                "model": model,
                "dataset": dataset,
                "exponent": f"{total:.6f}",
                "batch_term": f"{batch:.6f}",
                "step_term": f"{step:.6f}",
                "residual": f"{total - batch - step:.2e}",
                "seeds": min(s for _lr, s in solved.values()),
            })
    return out


def main() -> None:
    data = rows()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(data)
    print(f"wrote {len(data)} decompositions to {OUT}")
    for r in data:
        print(f"  {r['model']:>11}  {r['dataset']:<14} exponent {float(r['exponent']):.3f}"
              f" = batch {float(r['batch_term']):.3f} + step {float(r['step_term']):.3f}"
              f"   (residual {r['residual']}, seeds {r['seeds']})")


if __name__ == "__main__":
    main()
