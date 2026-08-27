"""Learning-rate sweep across the packing x step-count factorial.

Section 10.6 measured the optimal stage-1 learning rate at 3.0e-5 unpacked
(1,600 steps) and ~1.5e-4 packed (350 steps), and read the ~5x gap against the
4.53x supervised-token ratio as linear batch scaling. That reading has a
confound: the packed runs are shorter *because* they are packed, so batch size
went up 4.53x and the optimizer step count went down 4.57x at the same time.
The observation fits a second hypothesis with no batch-size content at all --
that what is conserved is the area under the learning-rate schedule
(3.0e-5 x 1600 = 0.048 against 1.5e-4 x 350 = 0.0525).

The two hypotheses make opposite predictions off the diagonal, so this script
runs the full 2x2:

                    350 steps          1,600 steps
    unpacked        lr* = ?            lr* = 3.0e-5   (known)
    packed          lr* = 1.5e-4       lr* = ?        (known)

  * batch scaling    -> lr* depends on the row only: packed sits ~5x above
                        unpacked at BOTH step counts.
  * schedule integral -> lr* depends on the column only: 350 steps sits ~4.6x
                        above 1,600 at BOTH batch sizes.

Holding the step count fixed and varying batch size means the smaller batch
sees less data, which is not a defect: seeing more data per update is what a
larger batch *is*. The unpacked 350-step cell is 0.22 of an Alpaca epoch and
the packed 1,600-step cell is 4.57 epochs, so the latter is the cell where
overfitting, not the learning rate, may set the minimum -- which is a result
either way, and is why every run reports its whole validation curve.

Validation is unpacked in both modes (the trainer enforces this), so every
number here is on one ruler and is directly comparable to the tables in
sections 10.4 and 10.6.

    python scripts/sweep_lr_packing.py                    # the full grid
    python scripts/sweep_lr_packing.py --dry-run          # print the plan
    python scripts/sweep_lr_packing.py --cells packed_1600
    python scripts/sweep_lr_packing.py --seeds 1337 1338 1339 --lrs 9e-5 1.5e-4

Results append to results/lr_scaling_sweep.csv as each run lands, so the file
is readable while the sweep is still going and a re-invocation skips whatever
is already in it.
"""

from __future__ import annotations

import argparse
import csv
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = REPO / ".venv" / "bin" / "python"
RESULTS = REPO / "results" / "lr_scaling_sweep.csv"
CONFIG_DIR = REPO / "configs" / "train" / "lr_scaling"
LOG_DIR = REPO / "logs" / "lr_scaling"
CKPT_DIR = REPO / "checkpoints" / "lr_scaling"

MODEL_CONFIG = REPO / "configs" / "model" / "medium.yaml"
INIT_FROM = REPO / "checkpoints" / "medium" / "step_20000.pt"
SFT_DIR = REPO / "data" / "sft"

# Both existing stage-1 configs warm up over ~6.25% of the schedule (22/350,
# 100/1600) and floor at max_lr/10. Holding both ratios fixed across the grid
# is what keeps the cells comparable: a cell whose warmup fraction drifted
# would confound the thing being measured.
WARMUP_FRACTION = 0.0625
MIN_LR_RATIO = 0.1
EVALS_PER_RUN = 7


@dataclass(frozen=True)
class Cell:
    """One corner of the factorial: a batch size and a step count.

    Batch size is set two different ways here. The four factorial corners hold
    gradient accumulation at 4 and change supervised tokens per step by turning
    packing on; the `wide` cell instead leaves packing off and raises
    accumulation until the tokens per step match the packed cell's. See
    WIDE_ACCUM.
    """

    name: str
    pack_sequences: bool
    max_steps: int
    # Roughly how long one run in this cell takes, for the plan printout only.
    minutes: float
    grad_accum: int = 4


@dataclass(frozen=True)
class Dataset:
    """A corpus, its packing ratio, and the two step counts that bracket it.

    The step counts are one packed epoch and one unpacked epoch, which is the
    pair the confound lives between: at a fixed data budget those two differ by
    exactly the packing ratio, so running the factorial over them is what pulls
    batch size and step count apart.
    """

    name: str
    filename: str
    short_steps: int   # one packed epoch
    long_steps: int    # one unpacked epoch
    packing_ratio: float
    # Median measured over the 97 runs in results/lr_scaling_sweep.csv, taken
    # from the runs that landed on an unthrottled card. See THROTTLE below --
    # these are per-GPU rates, not a fleet average, because the two cards in
    # this box are not the same speed under load.
    minutes_per_1600_unpacked: float
    minutes_per_1600_packed: float

    @property
    def path(self) -> Path:
        return SFT_DIR / self.filename


DATASETS = {
    d.name: d
    for d in (
        # Alpaca: 50,868 usable examples -> 11,220 windows at 4.5 per window.
        Dataset("alpaca", "alpaca.jsonl", short_steps=350, long_steps=1600,
                packing_ratio=4.53, minutes_per_1600_unpacked=18.4,
                minutes_per_1600_packed=19.6),
        # Dolly: 13,756 usable examples -> 4,341 windows at 3.2 per window.
        # A second packing ratio is what makes the scaling claim a curve rather
        # than a single point, and it is cheap: both step counts are short.
        # Slower per step than Alpaca despite the shorter cells: Dolly's
        # examples are longer, so a window carries more real tokens.
        Dataset("dolly", "dolly.jsonl", short_steps=136, long_steps=430,
                packing_ratio=3.16, minutes_per_1600_unpacked=20.6,
                minutes_per_1600_packed=22.5),
        # Alpaca's shortest and longest thirds by encoded length. Splitting one
        # corpus by length varies the packing ratio -- 7.85x and 2.73x against
        # the full corpus's 4.53x -- without changing where the data came from,
        # which is the control the Alpaca-versus-Dolly comparison cannot give.
        # Both subsets hold 16,956 training examples, so their unpacked epoch is
        # the same 530 steps and only the packed epoch moves.
        Dataset("alpaca_short", "alpaca_short.jsonl", short_steps=68, long_steps=530,
                packing_ratio=7.85, minutes_per_1600_unpacked=18.4,
                minutes_per_1600_packed=19.6),
        # The middle third is the control on the control: at 4.87x it packs almost
        # exactly like the full corpus (4.53x), so if length-stratification itself
        # distorted the measurement, this row would not land on Alpaca's.
        Dataset("alpaca_mid", "alpaca_mid.jsonl", short_steps=109, long_steps=530,
                packing_ratio=4.87, minutes_per_1600_unpacked=18.4,
                minutes_per_1600_packed=19.6),
        # A random third, not a length tercile. It matches the whole corpus on
        # packing ratio (4.51x against 4.53x), on padded batch (1,841 against
        # 1,888 supervised tokens/step) and on length distribution, while
        # matching the terciles on size and padded step count. Against the whole
        # corpus it therefore varies the scale of the run and nothing else,
        # which is the comparison the terciles cannot make.
        Dataset("alpaca_third", "alpaca_third.jsonl", short_steps=118, long_steps=530,
                packing_ratio=4.51, minutes_per_1600_unpacked=18.4,
                minutes_per_1600_packed=19.6),
        # The scale test on the second corpus: a random third of Dolly. Its
        # packing factor comes out at 3.14x against the whole corpus's 2.92x --
        # sampling variation in a smaller draw, and larger than the 1% the
        # Alpaca pair managed -- so the exponent is taken against each corpus's
        # own factor rather than a shared one.
        Dataset("dolly_third", "dolly_third.jsonl", short_steps=46, long_steps=143,
                packing_ratio=3.14, minutes_per_1600_unpacked=20.6,
                minutes_per_1600_packed=22.5),
        # Nested inside alpaca_third, which is nested inside the whole corpus:
        # ninth < third < all, each a random sample with the same length
        # distribution and the same ~4.5x packing factor, so the three of them
        # are a 9x scale series with everything else held.
        Dataset("alpaca_ninth", "alpaca_ninth.jsonl", short_steps=39, long_steps=177,
                packing_ratio=4.53, minutes_per_1600_unpacked=18.4,
                minutes_per_1600_packed=19.6),
        Dataset("alpaca_long", "alpaca_long.jsonl", short_steps=194, long_steps=530,
                packing_ratio=2.73, minutes_per_1600_unpacked=18.4,
                minutes_per_1600_packed=19.6),
    )
}


# Gradient accumulation that makes an unpacked step carry the same supervised
# tokens as a packed one: round(packed_tokens_per_step / unpacked_tokens_per
# _micro_batch), from the measured values above. Alpaca lands within 0.6% of
# its packed cell (8,496 against 8,444), Dolly within 2.8% (6,816 against
# 6,632); the residual is reported rather than corrected for.
WIDE_ACCUM = {"alpaca": 18, "dolly": 12, "alpaca_short": 31, "alpaca_mid": 19, "alpaca_long": 11,
              "alpaca_third": 18, "alpaca_ninth": 18, "dolly_third": 13}


def cells_for(dataset: Dataset) -> dict[str, Cell]:
    """The four corners of the factorial, plus the wide-batch control."""
    cells = {}
    for packed in (False, True):
        rate = dataset.minutes_per_1600_packed if packed else dataset.minutes_per_1600_unpacked
        for steps in (dataset.short_steps, dataset.long_steps):
            name = f"{'packed' if packed else 'unpacked'}_{steps}"
            cells[name] = Cell(name, pack_sequences=packed, max_steps=steps,
                               minutes=rate * steps / 1600)

    # The control the factorial alone cannot give. Packing raises supervised
    # tokens per step ~4.5x while leaving the number of forward-pass rows
    # unchanged; raising gradient accumulation instead reaches the same tokens
    # per step through 4.5x as many rows. Matched this way the two cells agree
    # on supervised tokens per step, on examples per step, and on data seen --
    # everything a batch-size rule could depend on -- and differ only in whether
    # those examples arrive packed into shared windows or padded into their own.
    # If lr* is set by the statistical batch, the two land on the same optimum.
    accum = WIDE_ACCUM[dataset.name]
    steps = dataset.short_steps
    name = f"wide_{steps}"
    cells[name] = Cell(name, pack_sequences=False, max_steps=steps,
                       minutes=dataset.minutes_per_1600_unpacked * steps * (accum / 4) / 1600,
                       grad_accum=accum)
    return cells


# Cards that run slower than GPU 0 under sustained load, as a multiplier on
# wall-clock. GPU 1 in this box drops to ~300MHz once it heats up; the sweep
# still uses it, but the schedule estimate has to know.
THROTTLE = {1: 2.5}

# ~1.5-1.7x spacing over a 25x span. Wide enough that both hypotheses' predicted
# optima (3.0e-5 and 1.5e-4 in the two new cells) sit inside the grid with room
# on either side, so an optimum is bracketed rather than pinned at an edge.
DEFAULT_LRS = [1.0e-5, 2.0e-5, 3.0e-5, 6.0e-5, 9.0e-5, 1.5e-4, 2.5e-4]

CONFIG_TEMPLATE = """# Generated by scripts/sweep_lr_packing.py -- do not edit by hand.
# Cell {cell} of the packing x step-count factorial, max_lr {max_lr}, seed {seed}.
batch_size: 8
seq_len: 512
grad_accum_steps: {grad_accum}
max_steps: {max_steps}
warmup_steps: {warmup_steps}
max_lr: {max_lr}
min_lr: {min_lr}
weight_decay: 0.1
betas: [0.9, 0.95]
grad_clip: 1.0
log_interval: 10
eval_interval: {eval_interval}
eval_steps: 40
checkpoint_interval: {max_steps}
amp_dtype: float16
gradient_checkpointing: false
use_efficient_attention: true
pack_sequences: {pack_sequences}
stride: null
num_workers: 3
pin_memory: true
compile_model: false
seed: {seed}
"""

FIELDNAMES = [
    "dataset",
    "cell",
    "pack_sequences",
    "max_steps",
    "max_lr",
    "seed",
    "final_val_loss",
    "best_val_loss",
    "best_val_step",
    "wall_seconds",
    "log_path",
]


def yaml_float(value: float) -> str:
    """Render a float so YAML 1.1 reads it back as a float.

    `3e-05` is a *string* to a YAML 1.1 parser -- the float pattern requires a
    decimal point and a signed exponent -- and a string learning rate reaches
    AdamW intact and fails there on a type comparison. `3.000e-05` parses.
    """
    return f"{value:.3e}"


def run_id(dataset: Dataset, cell: Cell, lr: float, seed: int) -> str:
    return f"{dataset.name}_{cell.name}_lr{lr:g}_seed{seed}"


def write_config(dataset: Dataset, cell: Cell, lr: float, seed: int) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"{run_id(dataset, cell, lr, seed)}.yaml"
    path.write_text(
        CONFIG_TEMPLATE.format(
            cell=cell.name,
            grad_accum=cell.grad_accum,
            max_steps=cell.max_steps,
            warmup_steps=max(1, round(cell.max_steps * WARMUP_FRACTION)),
            max_lr=yaml_float(lr),
            min_lr=yaml_float(lr * MIN_LR_RATIO),
            eval_interval=max(1, cell.max_steps // EVALS_PER_RUN),
            pack_sequences=str(cell.pack_sequences).lower(),
            seed=seed,
        )
    )
    return path


def read_val_curve(log_path: Path) -> list[tuple[int, float]]:
    """Every (step, val_loss) pair in a training log.

    Validation rows carry an empty train_loss, which is what distinguishes them
    from the per-step rows sharing the same file.
    """
    curve: list[tuple[int, float]] = []
    with log_path.open() as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("val_loss") or "").strip()
            if raw:
                curve.append((int(row["step"]), float(raw)))
    return curve


def completed_runs() -> set[tuple[str, float, int]]:
    if not RESULTS.exists():
        return set()
    with RESULTS.open() as handle:
        return {
            # Rows written before the sweep grew a second corpus have no
            # dataset column; they are all Alpaca.
            (row.get("dataset") or "alpaca", row["cell"], float(row["max_lr"]), int(row["seed"]))
            for row in csv.DictReader(handle)
        }


RUN_NAME = re.compile(
    r"^(?:(?P<dataset>alpaca|dolly)_)?"
    r"(?P<pack>packed|unpacked)_(?P<steps>\d+)_lr(?P<lr>[^_]+)_seed(?P<seed>\d+)$"
)


def harvest(lock: threading.Lock) -> int:
    """Append any finished run whose result never reached the results file.

    A run is a subprocess, so killing the driver -- to rebalance GPUs, say --
    leaves its children training happily with nobody left to record them. This
    recovers them from their logs afterwards, which is what makes stopping the
    sweep cheap: the only work ever lost is a run that had not finished.

    A run counts as finished when its last validation row sits at the step
    count its own name declares. Anything short of that was interrupted
    mid-flight and is left for a re-run.
    """
    done = completed_runs()
    recovered = 0
    for log_path in sorted(LOG_DIR.glob("*.csv")):
        match = RUN_NAME.match(log_path.stem)
        if not match:
            continue
        dataset = match["dataset"] or "alpaca"
        cell = f"{match['pack']}_{match['steps']}"
        lr, seed, steps = float(match["lr"]), int(match["seed"]), int(match["steps"])
        if (dataset, cell, lr, seed) in done:
            continue

        curve = read_val_curve(log_path)
        if not curve or curve[-1][0] != steps:
            continue

        best_step, best_loss = min(curve, key=lambda pair: pair[1])
        append_result(
            {
                "dataset": dataset,
                "cell": cell,
                "pack_sequences": match["pack"] == "packed",
                "max_steps": steps,
                "max_lr": lr,
                "seed": seed,
                "final_val_loss": f"{curve[-1][1]:.4f}",
                "best_val_loss": f"{best_loss:.4f}",
                "best_val_step": best_step,
                "wall_seconds": "",  # unknown: the driver that timed it is gone
                "log_path": str(log_path.relative_to(REPO)),
            },
            lock,
        )
        print(f"harvested {log_path.stem}: final {curve[-1][1]:.4f}")
        recovered += 1
    return recovered


def migrate_results() -> None:
    """Bring an existing results file up to the current column set.

    The sweep grew a `dataset` column once a second corpus was added. Appending
    new-format rows under an old header would silently shift every field one
    place, so the file is rewritten once, with pre-existing rows attributed to
    the corpus that was the only one at the time.

    Safe to call only when no sweep is running -- it rewrites the file another
    process may be appending to -- which is why it happens at startup, before
    any worker exists.
    """
    if not RESULTS.exists():
        return
    with RESULTS.open() as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames == FIELDNAMES:
            return
        rows = [{**row, "dataset": row.get("dataset") or "alpaca"} for row in reader]

    with RESULTS.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"migrated {len(rows)} existing rows in {RESULTS.name} to the dataset-aware header")


def append_result(row: dict, lock: threading.Lock) -> None:
    with lock:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        is_new = not RESULTS.exists()
        with RESULTS.open("a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            if is_new:
                writer.writeheader()
            writer.writerow(row)


def train_one(dataset: Dataset, cell: Cell, lr: float, seed: int, gpu: int,
              keep_checkpoints: bool, lock: threading.Lock) -> None:
    name = run_id(dataset, cell, lr, seed)
    config = write_config(dataset, cell, lr, seed)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.csv"
    ckpt_dir = CKPT_DIR / name
    stdout_path = LOG_DIR / f"{name}.out"

    command = [
        str(PYTHON),
        str(REPO / "scripts" / "finetune.py"),
        "--model", str(MODEL_CONFIG),
        "--train", str(config),
        "--init-from", str(INIT_FROM),
        "--data", str(dataset.path),
        "--checkpoint-dir", str(ckpt_dir),
        "--log-path", str(log_path),
    ]

    print(f"[gpu {gpu}] start {name}", flush=True)
    started = time.monotonic()
    with stdout_path.open("w") as sink:
        result = subprocess.run(
            command,
            cwd=REPO,
            stdout=sink,
            stderr=subprocess.STDOUT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu)},
        )
    elapsed = time.monotonic() - started

    if result.returncode != 0:
        print(f"[gpu {gpu}] FAILED {name} (exit {result.returncode}) -- see {stdout_path}", flush=True)
        return False

    curve = read_val_curve(log_path)
    if not curve:
        print(f"[gpu {gpu}] FAILED {name}: no validation rows in {log_path}", flush=True)
        return False

    best_step, best_loss = min(curve, key=lambda pair: pair[1])
    append_result(
        {
            "dataset": dataset.name,
            "cell": cell.name,
            "pack_sequences": cell.pack_sequences,
            "max_steps": cell.max_steps,
            "max_lr": lr,
            "seed": seed,
            "final_val_loss": f"{curve[-1][1]:.4f}",
            "best_val_loss": f"{best_loss:.4f}",
            "best_val_step": best_step,
            "wall_seconds": f"{elapsed:.0f}",
            "log_path": str(log_path.relative_to(REPO)),
        },
        lock,
    )
    print(
        f"[gpu {gpu}] done  {name}: final {curve[-1][1]:.4f}, "
        f"best {best_loss:.4f} @ {best_step} ({elapsed / 60:.1f} min)",
        flush=True,
    )

    # The weights answer nothing this sweep asks -- the validation curve is the
    # measurement -- and 28 of them is 42GB. Kept behind a flag for whichever
    # checkpoint the sweep ends up arguing for.
    if not keep_checkpoints and ckpt_dir.exists():
        shutil.rmtree(ckpt_dir)

    return True


def main() -> None:
    global RESULTS, INIT_FROM, MODEL_CONFIG, LOG_DIR, CONFIG_DIR, CKPT_DIR
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="alpaca", choices=list(DATASETS))
    parser.add_argument("--cells", nargs="+",
                        help="Cell names for this dataset; default is all four")
    parser.add_argument("--lrs", nargs="+", type=float, default=DEFAULT_LRS)
    parser.add_argument("--seeds", nargs="+", type=int, default=[1337])
    parser.add_argument("--gpus", nargs="+", type=int, default=[0, 1])
    parser.add_argument("--keep-checkpoints", action="store_true",
                        help="Keep each run's final weights (~1.5GB each)")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and exit")
    # A second model size writes to its own ledger: the dedup key is
    # (dataset, cell, lr, seed), which says nothing about the base model, so
    # sharing one file would let 124M and 30M runs silently collide.
    parser.add_argument("--results", type=Path, default=RESULTS,
                        help="Results CSV to append to and resume from")
    parser.add_argument("--init-from", type=Path, default=INIT_FROM,
                        help="Pretrained checkpoint each run starts from")
    parser.add_argument("--model-config", type=Path, default=MODEL_CONFIG,
                        help="Model preset being fine-tuned")
    parser.add_argument("--max-consecutive-failures", type=int, default=3,
                        help="abort the sweep after this many failures in a row "
                             "(0 disables); guards against one busy GPU turning "
                             "a whole grid into FAILED rows")
    parser.add_argument("--harvest", action="store_true",
                        help="Recover finished runs orphaned by a killed driver, then exit")
    args = parser.parse_args()
    RESULTS, INIT_FROM, MODEL_CONFIG = args.results, args.init_from, args.model_config

    # Namespace the per-run artefacts alongside the ledger. run_id is
    # (dataset, cell, lr, seed) and says nothing about the base model, so a
    # second model size writing into the same directories appends its curve to
    # the first model's log file -- which silently corrupts best_val_loss for
    # the new runs and pollutes the old ones. Namespacing is what stops that.
    tag = RESULTS.stem.replace("lr_scaling_", "")
    if tag != "sweep":
        LOG_DIR = LOG_DIR.parent / f"lr_scaling_{tag}"
        CONFIG_DIR = CONFIG_DIR.parent / f"lr_scaling_{tag}"
        CKPT_DIR = CKPT_DIR.parent / f"lr_scaling_{tag}"

    migrate_results()
    if args.harvest:
        count = harvest(threading.Lock())
        print(f"harvested {count} orphaned run(s)")
        return
    dataset = DATASETS[args.dataset]
    cells = cells_for(dataset)
    if args.cells:
        unknown = [name for name in args.cells if name not in cells]
        if unknown:
            raise SystemExit(f"{dataset.name} has cells {list(cells)}, not {unknown}")
        selected = args.cells
    else:
        selected = list(cells)

    if not dataset.path.exists():
        raise SystemExit(f"missing corpus {dataset.path}")

    done = completed_runs()
    plan = [
        (cells[cell], lr, seed)
        for cell in selected
        for lr in args.lrs
        for seed in args.seeds
        if (dataset.name, cell, lr, seed) not in done
    ]

    skipped = len(selected) * len(args.lrs) * len(args.seeds) - len(plan)
    serial_minutes = sum(cell.minutes for cell, _, _ in plan)
    # GPU 1 thermally throttles to ~300MHz under sustained load and takes about
    # 2.5x as long per step as GPU 0 (measured across this sweep: 2.31-2.54x by
    # cell). Dividing the serial estimate by the GPU count assumes they are
    # interchangeable and is off by ~3x for anything scheduled on GPU 1, so add
    # throughputs instead.
    throughput = sum(1.0 / THROTTLE.get(gpu, 1.0) for gpu in args.gpus) or 1.0
    print(f"dataset {dataset.name} (packing ratio {dataset.packing_ratio:.2f}x), "
          f"cells {selected}")
    print(f"{len(plan)} runs to go ({skipped} already in {RESULTS.name})")
    print(f"~{serial_minutes:.0f} min on one unthrottled gpu, ~{serial_minutes / throughput:.0f} min "
          f"across {len(args.gpus)} gpu(s)"
          + (f" (gpu {sorted(set(args.gpus) & set(THROTTLE))} throttled)"
             if set(args.gpus) & set(THROTTLE) else ""))

    if args.dry_run:
        for cell, lr, seed in plan:
            print(f"  {cell.name:<14} lr {lr:<8g} seed {seed}  (~{cell.minutes:.1f} min)")
        return

    # Longest runs first, so the two GPUs finish within one short run of each
    # other instead of one idling through the tail.
    plan.sort(key=lambda item: -item[0].minutes)

    work: queue.Queue = queue.Queue()
    for item in plan:
        work.put(item)

    lock = threading.Lock()

    # Circuit breaker. A run that dies for an environmental reason -- most often
    # CUDA OOM because another process still holds the card -- will usually kill
    # the next run too, and the next. Without this the sweep sprints through the
    # whole grid marking everything FAILED: 15 runs in 0.9 minutes, an empty
    # ledger, and the cause buried at the top of the log. Stop after a few in a
    # row and say so, so the operator fixes the machine rather than the grid.
    state = {"consecutive": 0, "tripped": False}

    def worker(gpu: int) -> None:
        while True:
            if state["tripped"]:
                return
            try:
                cell, lr, seed = work.get_nowait()
            except queue.Empty:
                return
            try:
                ok = train_one(dataset, cell, lr, seed, gpu, args.keep_checkpoints, lock)
                with lock:
                    if ok:
                        state["consecutive"] = 0
                    else:
                        state["consecutive"] += 1
                        if (args.max_consecutive_failures
                                and state["consecutive"] >= args.max_consecutive_failures):
                            state["tripped"] = True
                            print(
                                f"\nSTOPPING: {state['consecutive']} runs failed in a row. "
                                f"That is usually the machine and not the grid -- check "
                                f"nvidia-smi for a process still holding a card, and the most "
                                f"recent .out file for OOM. Nothing after this point was "
                                f"attempted; re-run to resume.",
                                flush=True,
                            )
            finally:
                work.task_done()

    threads = [threading.Thread(target=worker, args=(gpu,), daemon=True) for gpu in args.gpus]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"sweep finished in {(time.monotonic() - started) / 60:.1f} min -> {RESULTS}")


if __name__ == "__main__":
    main()
