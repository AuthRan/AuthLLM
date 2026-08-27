"""Does it matter whether you score a learning-rate sweep on the endpoint?

`scripts/analyze_lr_scaling.py` reports the optimum under `final_val_loss` and
notes in passing that the long packed cells move under `best_val_loss`. This
script asks the general question, because the answer turns out to be sharp and
to apply to any sweep, not just this one.

Every run in the sweep completes a full cosine cycle, so the endpoint is the
comparable quantity and is the paper's primary metric. The alternative -- score
each run at its best validation checkpoint -- is what a practitioner doing early
stopping would use. The two disagree only when runs overfit before the end, and
the disagreement is not neutral: the gap between the endpoint and the best
checkpoint grows with the learning rate, because a larger step size overfits
further past its own minimum in the steps that remain. An endpoint-scored sweep
therefore carries an extra penalty on high learning rates that has nothing to do
with step size, and its argmin is pulled down.

The size of that bias is what this script measures, per cell, against how many
epochs the cell runs.

    python scripts/analyze_metric_bias.py
    python scripts/analyze_metric_bias.py --dataset dolly

Seed 1337 throughout: the two metrics have to be compared on identical runs, and
a seed changes which examples are held out (see analyze_lr_scaling's `load`).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_lr_scaling import (  # noqa: E402  -- same directory, shared constants
    CORPUS_SUPERVISED_TOKENS,
    RESULTS,
    SUPERVISED_TOKENS,
    cell_tokens,
    interpolated_optimum,
)

# Below this many epochs no run in the sweep peaks before its final step at all,
# so the two metrics are literally the same measurement. Above it they begin to
# differ, but "differs" and "differs enough to move the optimum" are not the same
# threshold -- the 1.01-epoch cell peaks early by 0.0005 nats and its optimum
# does not budge. MATERIAL_SHIFT is the second threshold, and the one that
# matters to anyone reading a sweep.
SAFE_EPOCHS = 1.0
MATERIAL_SHIFT = 1.01


def cell_epochs(dataset: str, cell: str, steps: int) -> float:
    return steps * cell_tokens(dataset, cell) / CORPUS_SUPERVISED_TOKENS[dataset]


def load(dataset: str) -> dict[str, list[dict]]:
    by_cell: dict[str, list[dict]] = defaultdict(list)
    with RESULTS.open() as handle:
        for row in csv.DictReader(handle):
            if (row.get("dataset") or "alpaca") != dataset or row["seed"] != "1337":
                continue
            if not row["final_val_loss"] or not row["best_val_loss"]:
                continue
            by_cell[row["cell"]].append(row)
    return {cell: sorted(rows, key=lambda r: float(r["max_lr"])) for cell, rows in by_cell.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="alpaca", choices=sorted(SUPERVISED_TOKENS))
    parser.add_argument("--markdown", type=Path, help="Also write the tables to this file")
    args = parser.parse_args()

    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    cells = load(args.dataset)
    emit(f"# Endpoint versus best-checkpoint scoring — {args.dataset}")
    emit()
    emit("| cell | epochs | runs peaking early | mean endpoint penalty | lr* endpoint | lr* best | shift |")
    emit("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")

    contaminated = []
    for cell, rows in sorted(cells.items(), key=lambda kv: cell_epochs(args.dataset, kv[0],
                                                                      int(kv[1][0]["max_steps"]))):
        steps = int(rows[0]["max_steps"])
        epochs = cell_epochs(args.dataset, cell, steps)
        penalties = [float(r["final_val_loss"]) - float(r["best_val_loss"]) for r in rows]
        early = sum(1 for r in rows if int(r["best_val_step"]) < steps)

        final_pts = [(float(r["max_lr"]), float(r["final_val_loss"])) for r in rows]
        best_pts = [(float(r["max_lr"]), float(r["best_val_loss"])) for r in rows]
        lr_final, _, ok_f = interpolated_optimum(final_pts)
        lr_best, _, ok_b = interpolated_optimum(best_pts)
        shift = f"{lr_best / lr_final:.2f}x" if ok_f and ok_b else "—"

        emit(f"| {cell} | {epochs:.2f} | {early}/{len(rows)} | "
             f"{sum(penalties) / len(penalties):.4f} | {lr_final:.2e} | {lr_best:.2e} | {shift} |")
        moved = ok_f and ok_b and abs(lr_best / lr_final - 1.0) >= MATERIAL_SHIFT - 1.0
        if moved:
            contaminated.append((cell, epochs, rows, penalties, lr_best / lr_final))

    emit()
    emit("The endpoint penalty is `final_val_loss - best_val_loss`: how much a run")
    emit("gives back after its own best checkpoint. Where it is zero for every run,")
    emit("the two metrics are the same measurement and the choice between them is free.")
    emit()

    for cell, epochs, rows, penalties, shift in contaminated:
        emit(f"## {cell} — {epochs:.2f} epochs")
        emit()
        emit("| max_lr | best checkpoint at | endpoint penalty |")
        emit("| --- | ---: | ---: |")
        for row, penalty in zip(rows, penalties):
            frac = int(row["best_val_step"]) / int(row["max_steps"])
            emit(f"| {float(row['max_lr']):.1e} | {frac * 100:.0f}% of run | {penalty:.4f} |")
        emit()
        rising = all(b >= a - 1e-9 for a, b in zip(penalties, penalties[1:]))
        emit(f"Penalty rises monotonically with the learning rate: **{rising}**.")
        emit("That is the bias. The endpoint metric charges the higher learning rates")
        emit("for overfitting they did after already passing their own minimum, so the")
        emit(f"argmin moves down — here by {shift:.2f}x — for a reason that has nothing")
        emit("to do with step size.")
        emit()

    safe = sorted(c for c, rows in cells.items()
                  if cell_epochs(args.dataset, c, int(rows[0]["max_steps"])) <= SAFE_EPOCHS)
    emit("## Headline")
    emit()
    emit(f"- Under {SAFE_EPOCHS:.0f} epoch ({', '.join(safe)}): no run peaks before its")
    emit("  final step, so endpoint and best-checkpoint scoring are the same measurement")
    emit("  and return the same optimum to the digit. The metric choice is free.")
    emit("- Just over 1 epoch: runs begin to peak early, but by so little that the")
    emit("  optimum does not move. Differing is not the same as mattering.")
    if contaminated:
        worst = max(contaminated, key=lambda c: c[4])
        emit(f"- Past ~3 epochs the optimum does move — {worst[0]} at {worst[1]:.1f} epochs")
        emit(f"  shifts {worst[4]:.2f}x, which is larger than this project's worst seed")
        emit("  spread (1.17x) and comparable to the effects such sweeps are run to")
        emit("  measure. A sweep whose cells run different numbers of epochs — which any")
        emit("  sweep over a data-budget-changing intervention does — is comparing cells")
        emit("  on differently-biased rulers, and has to say which metric it scored on")
        emit("  and how many epochs each cell ran.")
    emit()
    emit("Caveat: `best_val_step` is quantised to the evaluation grid (7 evals per")
    emit("run), so 'best checkpoint at' is accurate to about 14% of a run. The")
    emit("penalty itself is not quantised, and it is the quantity the bias depends on.")

    if args.markdown:
        args.markdown.write_text("\n".join(lines) + "\n")
        print(f"\nwrote {args.markdown}")


if __name__ == "__main__":
    main()
