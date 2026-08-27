"""Seed-replicate whichever packing-ratio subsets have already been swept.

The factorial's cells carry three seeds each; the subsets that make up the
packing-ratio series were swept at seed 1337 only, because their job was to say
whether the exponent moves at all. It moves a long way, so they now need the
same treatment the factorial got: the three learning rates bracketing each
cell's argmin, re-run at two more seeds, with each seed's curve solved on its own
(scripts/analyze_lr_scaling.py explains why an average across seeds would be the
wrong operation).

Which three learning rates depends on where the argmin landed, so this reads the
results file rather than being told:

    python scripts/replicate_series_seeds.py --dry-run
    python scripts/replicate_series_seeds.py --gpus 0 1

It shells out to scripts/sweep_lr_packing.py once per cell, so every run lands
in the same resumable ledger and nothing already present is repeated.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_lr_scaling  # noqa: E402
from analyze_lr_scaling import STEP_COUNTS, cell_name, load  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
# The length terciles, then the nested random samples that make up the scale
# series. Both carry claims the paper leans on, so both need more than one seed.
SUBSETS = ["alpaca_short", "alpaca_mid", "alpaca_long", "alpaca_third",
           "alpaca_ninth", "dolly_third"]
SEEDS = ["1338", "1339"]


def bracketing_lrs(points: list[tuple[float, float]]) -> list[float] | None:
    """The argmin and its two neighbours, or None if the argmin is at an edge.

    An edge argmin means the cell is unbracketed and its optimum is not a
    measurement, so replicating it would buy nothing.
    """
    points = sorted(points)
    index = min(range(len(points)), key=lambda i: points[i][1])
    if index == 0 or index == len(points) - 1:
        return None
    return [points[i][0] for i in (index - 1, index, index + 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gpus", nargs="+", default=["0", "1"])
    parser.add_argument("--dry-run", action="store_true")
    # A second model size has its own ledger and its own base checkpoint; the
    # cells to replicate are found in that ledger, and the runs are launched
    # against that model.
    parser.add_argument("--datasets", nargs="+", default=SUBSETS)
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--model-config", type=Path, default=None)
    parser.add_argument("--init-from", type=Path, default=None)
    args = parser.parse_args()

    datasets = args.datasets
    passthrough: list[str] = []
    if args.results:
        analyze_lr_scaling.RESULTS = args.results
        passthrough += ["--results", str(args.results)]
    if args.model_config:
        passthrough += ["--model-config", str(args.model_config)]
    if args.init_from:
        passthrough += ["--init-from", str(args.init_from)]

    planned = 0
    for dataset in datasets:
        if dataset not in STEP_COUNTS:
            continue
        short, long = STEP_COUNTS[dataset]
        curves = load("final_val_loss", dataset)
        for cell in (cell_name(True, short), cell_name(False, long)):
            by_seed = curves.get(cell)
            if not by_seed or 1337 not in by_seed:
                print(f"skip {dataset}/{cell}: not swept yet")
                continue
            lrs = bracketing_lrs(by_seed[1337])
            if lrs is None:
                print(f"skip {dataset}/{cell}: argmin sits at a grid edge, nothing to bracket")
                continue

            command = [
                sys.executable, str(REPO / "scripts" / "sweep_lr_packing.py"),
                "--dataset", dataset, "--cells", cell,
                "--lrs", *[f"{lr:g}" for lr in lrs],
                "--seeds", *SEEDS,
                "--gpus", *args.gpus,
                *passthrough,
            ]
            planned += len(lrs) * len(SEEDS)
            print(f"\n=== {dataset}/{cell}  lrs {[f'{lr:g}' for lr in lrs]} ===")
            if args.dry_run:
                print("  " + " ".join(command))
                continue
            subprocess.run(command, cwd=REPO, check=False)

    print(f"\n{planned} runs planned across {len(datasets)} datasets"
          f"{' (dry run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
