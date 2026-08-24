"""Read the packing x step-count sweep and say which hypothesis it supports.

`scripts/sweep_lr_packing.py` fills a 2x2 of (batch size) x (optimizer steps),
sweeping the learning rate in each cell. Two hypotheses explain the ~5x gap
section 10.6 originally measured, and they are separated by where the optimum
sits in the two cells that section never ran:

  * batch scaling     -- lr* is set by supervised tokens per step. Packed sits
                         ~4.5x above unpacked at BOTH step counts, and the two
                         step counts agree with each other.
  * schedule integral -- lr* is set by 1/steps, so that max_lr x steps is
                         conserved. 350 steps sits ~4.6x above 1,600 at BOTH
                         batch sizes, and the two batch sizes agree.

The ratios are what decide it, so this prints both: the batch effect (packed
over unpacked, holding steps fixed) and the step effect (350 over 1,600,
holding batch fixed). Whichever effect is ~4.5x while the other is ~1x is the
one driving the learning rate.

An optimum read off a coarse grid snaps to whichever point happened to be
sampled, so each cell's optimum is also interpolated: a parabola through the
argmin and its two neighbours in log(lr), which is the shape a loss-vs-log-lr
curve has near its minimum. The grid argmin is printed beside it, because when
the two disagree by more than the grid spacing it means the parabola is being
fit to something that is not a minimum.

    python scripts/analyze_lr_scaling.py
    python scripts/analyze_lr_scaling.py --metric best_val_loss
    python scripts/analyze_lr_scaling.py --markdown results/lr-scaling.md
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results" / "lr_scaling_sweep.csv"

# Rows are batch sizes, columns are step counts, so a row-wise difference is
# the batch effect and a column-wise one is the step effect.
# Each corpus has its own pair of step counts -- one packed epoch and one
# unpacked epoch -- so the factorial's columns are dataset-specific.
STEP_COUNTS = {"alpaca": (350, 1600), "dolly": (136, 430)}

# Supervised tokens per optimizer step, and the packing ratio each pair implies.
# Measured by scripts/benchmark_packing.py: the ratio of supervised tokens per
# step, which is NOT the ratio of windows (Dolly: 2.92x tokens against 3.16x
# windows, because packed windows are not perfectly full and lengths vary).
PACKING_RATIO = {"alpaca": 4.47, "dolly": 2.92}

# Supervised tokens per optimizer step, both measured by
# scripts/benchmark_packing.py at micro-batch 8 and multiplied by the 4 steps of
# gradient accumulation: Alpaca 472/2,111 per micro-batch, Dolly 568/1,658.
SUPERVISED_TOKENS = {"alpaca": {False: 1888, True: 8444}, "dolly": {False: 2272, True: 6632}}

# Usable examples x mean supervised tokens per example (568/8 = 71 for Dolly,
# 472/8 = 59 for Alpaca), for reporting each cell in epochs.
CORPUS_SUPERVISED_TOKENS = {"alpaca": 50868 * 59, "dolly": 13756 * 71}


def cell_name(packed: bool, steps: int) -> str:
    return f"{'packed' if packed else 'unpacked'}_{steps}"


def load(metric: str, dataset: str) -> dict[str, dict[int, list[tuple[float, float]]]]:
    """{cell: {seed: [(lr, loss), ...]}} sorted by lr.

    Curves are kept separate per seed and never averaged pointwise. The
    fine-tune derives its held-out split from the same seed it trains with
    (`scripts/finetune.py` line 90), so two seeds score on *different* validation
    examples and their losses differ by a constant offset -- ~0.10 nats between
    seeds 1337 and 1338 on Dolly. Within one seed that offset is shared by every
    learning rate and cancels out of the argmin; across seeds it does not.

    Averaging pointwise would be actively misleading here, because the extra
    seeds were run only on the three learning rates bracketing each argmin. Any
    seed with a lower offset would drag exactly those points down and confirm
    the argmin that selected them.
    """
    if not RESULTS.exists():
        raise SystemExit(f"no results yet at {RESULTS}")

    curves: dict[str, dict[int, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    with RESULTS.open() as handle:
        for row in csv.DictReader(handle):
            value = (row.get(metric) or "").strip()
            if value and (row.get("dataset") or "alpaca") == dataset:
                curves[row["cell"]][int(row["seed"])].append((float(row["max_lr"]), float(value)))
    return {cell: {seed: sorted(pts) for seed, pts in by_seed.items()}
            for cell, by_seed in curves.items()}


def cell_optimum(by_seed: dict[int, list[tuple[float, float]]]) -> tuple[float, float, bool, int]:
    """Optimum for one cell: each seed solved on its own curve, then averaged.

    Returns (geometric-mean lr*, spread as max/min across seeds, bracketed, n_seeds).
    A seed contributes only if its own curve brackets a minimum, so a seed run
    on three points either agrees that the middle one is best or is discarded.
    """
    per_seed = []
    for seed, points in sorted(by_seed.items()):
        if len(points) < 3:
            continue
        best, _grid, bracketed = interpolated_optimum(points)
        if bracketed:
            per_seed.append(best)
    if not per_seed:
        # Fall back to the fullest single curve so an unbracketed cell still reports.
        points = max(by_seed.values(), key=len)
        best, grid, bracketed = interpolated_optimum(points)
        return best, 1.0, bracketed, 1
    log_mean = sum(math.log(v) for v in per_seed) / len(per_seed)
    spread = max(per_seed) / min(per_seed) if len(per_seed) > 1 else 1.0
    return math.exp(log_mean), spread, True, len(per_seed)


def interpolated_optimum(points: list[tuple[float, float]]) -> tuple[float, float, bool]:
    """Optimal lr, its grid argmin, and whether the minimum is bracketed.

    Fits a parabola in log(lr) through the best grid point and its neighbours.
    A minimum sitting at either end of the grid is not bracketed: the true
    optimum is somewhere outside the range that was swept, and the returned
    value is the edge point rather than an extrapolation.
    """
    index = min(range(len(points)), key=lambda i: points[i][1])
    grid_best = points[index][0]

    if index == 0 or index == len(points) - 1:
        return grid_best, grid_best, False

    (x1, y1), (x2, y2), (x3, y3) = (
        (math.log(points[i][0]), points[i][1]) for i in (index - 1, index, index + 1)
    )
    # Vertex of the parabola through three points, in log space.
    denominator = (x1 - x2) * (x1 - x3) * (x2 - x3)
    a = (x3 * (y2 - y1) + x2 * (y1 - y3) + x1 * (y3 - y2)) / denominator
    b = (x3 * x3 * (y1 - y2) + x2 * x2 * (y3 - y1) + x1 * x1 * (y2 - y3)) / denominator
    if a <= 0:  # concave: the three points do not describe a minimum
        return grid_best, grid_best, False
    return math.exp(-b / (2 * a)), grid_best, True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="alpaca", choices=list(STEP_COUNTS))
    parser.add_argument("--metric", default="final_val_loss",
                        choices=["final_val_loss", "best_val_loss"])
    parser.add_argument("--markdown", type=Path, help="Also write the tables to this file")
    args = parser.parse_args()

    columns = STEP_COUNTS[args.dataset]
    tokens = SUPERVISED_TOKENS[args.dataset]
    curves = load(args.metric, args.dataset)
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# Learning-rate scaling under packing — {args.dataset} ({args.metric})")
    emit()

    # Per-cell curves, so a reader can see whether an optimum is real or noise.
    for packed in (False, True):
        for steps in columns:
            name = cell_name(packed, steps)
            by_seed = curves.get(name)
            if not by_seed:
                continue
            points = by_seed.get(1337) or max(by_seed.values(), key=len)
            epochs = steps * tokens[packed] / CORPUS_SUPERVISED_TOKENS[args.dataset]
            emit(f"## {name}  ({tokens[packed]:,} supervised tokens/step, "
                 f"{steps} steps, ~{epochs:.2f} epochs)")
            emit()
            emit("| max_lr | " + " | ".join(f"{lr:.1e}" for lr, _ in points) + " |")
            emit("| --- | " + " | ".join("---:" for _ in points) + " |")
            emit(f"| {args.metric} | " + " | ".join(f"{loss:.4f}" for _, loss in points) + " |")
            emit()

    optima: dict[str, float] = {}
    emit("## Optimum per cell")
    emit()
    emit("| cell | lr* (per-seed, geometric mean) | seeds | spread (max/min) | bracketed |")
    emit("| --- | ---: | ---: | ---: | :---: |")
    for packed in (False, True):
        for steps in columns:
            name = cell_name(packed, steps)
            by_seed = curves.get(name)
            if not by_seed:
                continue
            best, spread, bracketed, n = cell_optimum(by_seed)
            optima[name] = best
            emit(f"| {name} | {best:.2e} | {n} | {spread:.2f}x | "
                 f"{'yes' if bracketed else 'NO — at grid edge'} |")
    emit()

    emit("## The two effects")
    emit()
    emit("| effect | comparison | ratio |")
    emit("| --- | --- | ---: |")

    batch_ratios = []
    for steps in columns:
        packed_name, unpacked_name = cell_name(True, steps), cell_name(False, steps)
        if packed_name in optima and unpacked_name in optima:
            ratio = optima[packed_name] / optima[unpacked_name]
            batch_ratios.append(ratio)
            emit(f"| batch | packed / unpacked at {steps} steps | {ratio:.2f}x |")

    step_ratios = []
    for packed in (False, True):
        short, long = cell_name(packed, columns[0]), cell_name(packed, columns[1])
        if short in optima and long in optima:
            ratio = optima[short] / optima[long]
            step_ratios.append(ratio)
            label = "packed" if packed else "unpacked"
            emit(f"| steps | {columns[0]} / {columns[1]} steps, {label} | {ratio:.2f}x |")
    emit()

    # The headline numbers deliberately exclude any cell running well past one
    # epoch. In those cells the optimum is set by which learning rate overfits
    # least by the last step, not by which one takes the best-sized step, and
    # averaging them into a mean silently inverts the verdict.
    OVERFIT_EPOCHS = 1.5
    contaminated = {
        cell_name(packed, steps)
        for packed in (False, True)
        for steps in columns
        if steps * tokens[packed] / CORPUS_SUPERVISED_TOKENS[args.dataset] > OVERFIT_EPOCHS
    }
    if contaminated:
        emit(f"Excluded from the headline as overfitting-contaminated "
             f"(>{OVERFIT_EPOCHS} epochs): {', '.join(sorted(contaminated))}.")
        emit()

    short, long = columns
    token_ratio = tokens[True] / tokens[False]
    clean_batch = cell_name(True, short) not in contaminated and cell_name(False, short) not in contaminated

    emit("## Headline")
    emit()
    if clean_batch and cell_name(True, short) in optima and cell_name(False, short) in optima:
        ratio = optima[cell_name(True, short)] / optima[cell_name(False, short)]
        emit(f"- **Batch effect at {short} steps: {ratio:.2f}x** for a {token_ratio:.2f}x token "
             f"ratio — exponent {math.log(ratio) / math.log(token_ratio):.3f} "
             f"(linear 1.0, square-root 0.5).")
        emit("  Note this comparison still varies data seen: at a fixed step count the larger")
        emit("  batch consumes proportionally more of the corpus, and that residual confound")
        emit("  grows with the packing factor.")
    if cell_name(False, long) in optima and cell_name(False, short) in optima:
        ratio = optima[cell_name(False, short)] / optima[cell_name(False, long)]
        emit(f"- **Step effect (padded): {ratio:.2f}x** for a {long / short:.2f}x step ratio — "
             f"exponent {math.log(ratio) / math.log(long / short):.3f} in 1/steps.")

    # The diagonal is the comparison a practitioner actually makes: one packed
    # epoch against one padded epoch, same corpus, same data budget.
    if cell_name(True, short) in optima and cell_name(False, long) in optima:
        ratio = optima[cell_name(True, short)] / optima[cell_name(False, long)]
        emit(f"- **Matched data budget (one epoch each): {ratio:.2f}x** for a {token_ratio:.2f}x "
             f"packing factor — exponent {math.log(ratio) / math.log(token_ratio):.3f}.")
        emit("  This is the number that decides whether inheriting a learning rate is safe.")
    emit()

    # ---- the wide-batch control -------------------------------------------
    # packed_<short> raises supervised tokens per step by packing; wide_<short>
    # reaches the same tokens per step with the same examples per step and the
    # same data seen, by raising gradient accumulation instead. Whatever a
    # batch-size rule is a function of, the two cells agree on it. They differ
    # only in whether those examples arrive packed into shared windows or padded
    # into their own, which costs ~4.5x the forward-pass rows for the same
    # gradient. If lr* is set by the statistical batch, the ratio below is 1.
    wide = f"wide_{short}"
    packed_short = cell_name(True, short)
    if wide in optima and packed_short in optima:
        ratio = optima[wide] / optima[packed_short]
        emit("## The wide-batch control")
        emit()
        emit(f"- `{wide}` (unpacked, accumulation raised until tokens/step match) sits at "
             f"**{optima[wide]:.2e}**.")
        emit(f"- `{packed_short}` (the same batch reached by packing) sits at "
             f"**{optima[packed_short]:.2e}**.")
        emit(f"- Ratio **{ratio:.2f}x**. A statistical-batch rule predicts 1.00x; a rule keyed")
        emit(f"  on forward-pass rows rather than supervised tokens predicts "
             f"{token_ratio:.2f}x.")
        emit()
        if abs(math.log(ratio)) < math.log(1.20):
            emit("  The two agree. Packing's effect on the optimum is the batch-size effect of")
            emit("  the examples it fits into a step, not a property of packed representation:")
            emit("  the same optimum is reached by padding, at ~4.5x the compute.")
        else:
            emit("  The two disagree, which a batch-size rule does not predict. Something")
            emit("  about the packed representation, and not the size of the batch it")
            emit("  carries, is moving the optimum.")
        emit()

    if args.markdown:
        args.markdown.write_text("\n".join(lines) + "\n")
        print(f"\nwrote {args.markdown}")


if __name__ == "__main__":
    main()
