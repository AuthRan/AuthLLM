"""Score section 4.3's three conditions on something other than the corpus they trained on.

`results/registered-prediction-downstream.md` was written before any of this
ran. It asks whether the conclusion of section 4.3 -- that retuning the learning
rate under packing is worth 0.050 nats against inheriting it -- is a statement
about quality or a statement about held-out loss on the corpus that was
fine-tuned on.

The three conditions, at 124M on Alpaca at a matched data budget of one epoch,
seed 1337:

    A  unpacked_1600 @ 3e-5     padded at its optimum
    B  packed_350    @ 3e-5     packed at the inherited rate
    C  packed_350    @ 1.5e-4   packed at its own optimum

`scripts/sweep_lr_packing.py --keep-checkpoints` produces them. This scores all
three, and the base model beside them, on two held-out splits in one invocation
each so every column sees the same examples:

  * Alpaca, which is what section 4.3 used, as a reproduction check
  * Dolly, which none of them was fine-tuned on

    python scripts/run_downstream_eval.py
    python scripts/run_downstream_eval.py --n-prompts 80   # tighter behaviour
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = REPO / ".venv" / "bin" / "python"
CKPT_ROOT = REPO / "checkpoints" / "lr_scaling_downstream"

# label -> the sweep's run_id, which is also its checkpoint directory.
CONDITIONS = {
    "A_padded_optimum": "alpaca_unpacked_1600_lr3e-05_seed1337",
    "B_packed_inherited": "alpaca_packed_350_lr3e-05_seed1337",
    "C_packed_retuned": "alpaca_packed_350_lr0.00015_seed1337",
}

# What section 4.3 reports for each, so the validity check is in the code rather
# than in someone's memory of the table.
SECTION_43 = {"A_padded_optimum": 2.0720,
              "B_packed_inherited": 2.0678,
              "C_packed_retuned": 2.0175}

BASE = REPO / "checkpoints" / "medium" / "step_20000.pt"


def final_checkpoint(run_id: str) -> Path:
    """The last step this run saved, whatever the eval interval happened to be."""
    directory = CKPT_ROOT / run_id
    if not directory.is_dir():
        raise SystemExit(f"{directory} missing; run the sweep with --keep-checkpoints")
    steps = []
    for path in directory.glob("step_*.pt"):
        match = re.fullmatch(r"step_(\d+)\.pt", path.name)
        if match:
            steps.append((int(match.group(1)), path))
    if not steps:
        raise SystemExit(f"no step_*.pt under {directory}")
    return max(steps)[1]


def run_eval(corpus: str, checkpoints: dict[str, Path], args) -> str:
    out_path = REPO / "results" / f"downstream-eval-{corpus}.md"
    command = [str(PYTHON), str(REPO / "scripts" / "eval_instruction_following.py"),
               "--data", str(REPO / "data" / "sft" / f"{corpus}.jsonl"),
               "--split-seed", "1337", "--val-fraction", "0.02",
               "--n-prompts", str(args.n_prompts),
               "--loss-batches", str(args.loss_batches),
               "--output", str(out_path)]
    for label, path in checkpoints.items():
        command += ["--checkpoint", f"{label}={path}"]
    print(f"\n=== scoring on {corpus} held-out ===", flush=True)
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True)
    print(result.stdout[-4000:])
    if result.returncode != 0:
        print(result.stderr[-3000:], file=sys.stderr)
        raise SystemExit(f"eval failed on {corpus}")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-prompts", type=int, default=40)
    parser.add_argument("--loss-batches", type=int, default=40)
    parser.add_argument("--corpora", nargs="+", default=["alpaca", "dolly"])
    args = parser.parse_args()

    checkpoints = {"base": BASE}
    for label, run_id in CONDITIONS.items():
        checkpoints[label] = final_checkpoint(run_id)
    for label, path in checkpoints.items():
        print(f"  {label:<20} {path.relative_to(REPO)}")

    for corpus in args.corpora:
        run_eval(corpus, checkpoints, args)

    print("\nThe validity check the registration puts first: the ledger's")
    print("final_val_loss for these three runs must match section 4.3 to 0.005 nats.")
    for label, expected in SECTION_43.items():
        print(f"  {label:<20} section 4.3 reports {expected:.4f}")


if __name__ == "__main__":
    main()
