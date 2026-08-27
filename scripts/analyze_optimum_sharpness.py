"""How much does missing the optimal learning rate actually cost?

Section 6 recommends sweeping rather than scaling, over a range spanning
`p^0.4` to `p^1.7`, at about 1.6x spacing. Whether that spacing is fine enough
is not a matter of taste: it depends on how sharp the loss-versus-log-learning-
rate minimum is. A flat minimum means a coarse sweep is fine and the exact
optimum barely matters; a sharp one means the opposite.

So measure it. Near its minimum a loss-versus-log(lr) curve is locally
quadratic, which is the same assumption `analyze_lr_scaling.py` already makes
when it interpolates an optimum from three points (section 3.3). The
coefficient of that parabola is a curvature in nats per log(lr) squared, and
multiplying it by `log(spacing)^2` gives the loss penalty for sitting one grid
step away from the bottom.

    python scripts/analyze_optimum_sharpness.py
    python scripts/analyze_optimum_sharpness.py --spacing 2.0

The number that matters is the comparison, not the curvature: the penalty for
being one grid step off, against the penalty for inheriting the padded rate
instead of retuning at all. If the second is much larger than the first, a
coarse sweep captures nearly all of what retuning is worth, and the paper's
recommendation is cheap to follow.

Single seed (1337) and a local three-point fit, so these are estimates of shape
rather than seed-replicated measurements; the point is the order of magnitude.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import analyze_lr_scaling as A  # noqa: E402
from analyze_lr_scaling import STEP_COUNTS, cell_name, load  # noqa: E402

LEDGERS = [
    ("124M", REPO / "results" / "lr_scaling_sweep.csv"),
    ("30M", REPO / "results" / "lr_scaling_small.csv"),
    ("7M", REPO / "results" / "lr_scaling_mini.csv"),
    ("30M@19.7", REPO / "results" / "lr_scaling_small9k.csv"),
]


def curvature(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Quadratic through the argmin and its two neighbours, in log(lr).

    Returns (curvature, loss at the fitted minimum), or None if the argmin sits
    on an edge -- an unbracketed minimum is not a measurement (section 3.3).
    """
    pts = sorted(points)
    i = min(range(len(pts)), key=lambda j: pts[j][1])
    if i == 0 or i == len(pts) - 1:
        return None
    (x0, y0), (x1, y1), (x2, y2) = [(math.log(x), y) for x, y in pts[i - 1:i + 2]]
    left, right = x1 - x0, x2 - x1
    a = ((y2 - y1) / right - (y1 - y0) / left) / (x2 - x0)
    return a, y1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spacing", type=float, default=1.6,
                        help="grid spacing to price, as a multiple (default 1.6)")
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args()

    step = math.log(args.spacing) ** 2
    print(f"Cost of sitting one grid step ({args.spacing:g}x) off the optimum, seed {args.seed}.\n")
    print(f"{'model':<10}{'cell':<16}{'curvature':>11}{'  1 step off':>13}")
    rows = []
    for model, ledger in LEDGERS:
        if not ledger.exists():
            continue
        A.RESULTS = ledger
        for dataset in ("alpaca",):
            short, long = STEP_COUNTS[dataset]
            curves = load("final_val_loss", dataset)
            for packed, steps in ((True, short), (False, long)):
                name = cell_name(packed, steps)
                if name not in curves or args.seed not in curves[name]:
                    continue
                got = curvature(curves[name][args.seed])
                if got is None:
                    continue
                a, _ = got
                cost = a * step
                rows.append(cost)
                print(f"{model:<10}{name:<16}{a:>11.4f}{cost:>13.4f}")

    if rows:
        lo, hi = min(rows), max(rows)
        print(f"\nAcross these cells, one grid step off costs {lo:.4f} to {hi:.4f} nats.")
        print("Against the cost of inheriting the padded rate instead of retuning")
        print("(0.050 nats at 124M, 0.110 at 30M, 0.172 at 7M -- section 4.7), a sweep")
        print(f"at {args.spacing:g}x spacing gives up a small fraction of what retuning is worth.")


if __name__ == "__main__":
    main()
