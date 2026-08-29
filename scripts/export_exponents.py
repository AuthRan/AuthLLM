"""Emit one row per matched-budget comparison, across every ledger.

`analyze_packing_series.py` prints prose and markdown for people. This writes the
same numbers as a CSV for things that draw them -- currently
`ashugpt/viz/figures.py`, which must not import from `scripts/`. Having the
figure read this rather than recompute the optima keeps one implementation of
the estimator, so a change to how an optimum is solved cannot leave the picture
disagreeing with the text.

    python scripts/export_exponents.py            # -> results/exponents.csv

Columns: model, dataset, train_examples, packing_factor, lr_padded, lr_packed,
shift, exponent, bound, seeds.
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
OUT = REPO / "results" / "exponents.csv"

# Ledger per model, largest first. NOT all at the same tokens-per-parameter
# ratio: the 124M model saw 19.9 per parameter and the 30M and 7M runs were
# budgeted at 39.4 from a stale note. `30M@19.7` is the same 30M weights taken
# from the checkpoint that matches the 124M budget, so the pair brackets the
# error rather than hiding it.
LEDGERS = [
    ("124M", REPO / "results" / "lr_scaling_sweep.csv"),
    ("30M", REPO / "results" / "lr_scaling_small.csv"),
    ("7M", REPO / "results" / "lr_scaling_mini.csv"),
    # The same 30M and 7M weights at roughly half the pretraining, which is the
    # budget the 124M model actually got per parameter. "30M" and "7M" above are
    # the over-trained ones; see section 4.7.1 of the paper. The 7M run's
    # checkpoint interval did not land on the exact match, so its control is at
    # 18.0 tokens per parameter against the 124M run's 19.9 rather than 19.7.
    ("30M@19.7", REPO / "results" / "lr_scaling_small9k.csv"),
    ("7M@18.0", REPO / "results" / "lr_scaling_mini2k.csv"),
    # The 124M model taken from an early pretraining checkpoint, at perplexity
    # 107.0 against the 7M model's final 115.2. Same parameter count as "124M",
    # matched instead on base-model quality to the smallest model: the control
    # that separates size from quality (results/registered-prediction-size-vs-quality.md).
    ("124M@ppl107", REPO / "results" / "lr_scaling_quality.csv"),
    # The middle point of that quality axis: the same 124M weights at
    # perplexity 39.4, between the converged model's 23.5 and the step_500
    # checkpoint's 107.0. Registered before it ran, in
    # results/registered-prediction-quality-midpoint.md, because a control with
    # one offset and a control with two are different objects.
    ("124M@ppl39", REPO / "results" / "lr_scaling_quality2500.csv"),
]

# Training examples behind each corpus, for the scale axis.
EXAMPLES = {
    "alpaca": 50868, "alpaca_third": 16956, "alpaca_ninth": 5652,
    "alpaca_short": 16956, "alpaca_mid": 16956, "alpaca_long": 16956,
    "dolly": 13756, "dolly_third": 4585,
}

FIELDS = ["model", "dataset", "train_examples", "packing_factor",
          "lr_padded", "lr_packed", "shift", "exponent", "bound", "seeds"]


def rows() -> list[dict]:
    out = []
    for model, path in LEDGERS:
        if not path.exists():
            continue
        A.RESULTS = path
        for dataset, (short, long) in STEP_COUNTS.items():
            curves = load("final_val_loss", dataset)
            packed, unpacked = cell_name(True, short), cell_name(False, long)
            if packed not in curves or unpacked not in curves:
                continue
            lr_p, spread_p, ok_p, seeds_p = cell_optimum(curves[packed])
            lr_u, spread_u, ok_u, seeds_u = cell_optimum(curves[unpacked])
            if not (ok_p and ok_u):
                continue
            tokens = SUPERVISED_TOKENS[dataset]
            factor = tokens[True] / tokens[False]
            out.append({
                "model": model,
                "dataset": dataset,
                "train_examples": EXAMPLES.get(dataset, ""),
                "packing_factor": f"{factor:.4f}",
                "lr_padded": f"{lr_u:.6e}",
                "lr_packed": f"{lr_p:.6e}",
                "shift": f"{lr_p / lr_u:.4f}",
                # Six decimals, not four: the paper quotes these to three, and a
                # value like 0.51654 stored as "0.5165" reads back as 0.516
                # rather than 0.517. Anything that rounds a number the prose
                # quotes must keep more digits than the prose does.
                "exponent": f"{math.log(lr_p / lr_u) / math.log(factor):.6f}",
                "bound": f"{math.hypot(math.log(spread_p), math.log(spread_u)) / math.log(factor):.6f}",
                "seeds": min(seeds_p, seeds_u),
            })
    return out


def main() -> None:
    data = rows()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(data)
    print(f"wrote {len(data)} comparisons to {OUT}")
    for r in data:
        print(f"  {r['model']:>5}  {r['dataset']:<14} p={float(r['packing_factor']):.2f}x  "
              f"exponent {float(r['exponent']):.3f} ± {float(r['bound']):.3f}  seeds {r['seeds']}")


if __name__ == "__main__":
    main()
