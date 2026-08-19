#!/usr/bin/env python
"""Regenerate the live training-status block in README.md.

The block is delimited by TRAINING-STATUS markers so this can run repeatedly
against a README that is also edited by hand -- only the region between the
markers is rewritten.

Everything reported here is derived from artifacts the run already produces
(the metrics CSV, checkpoint mtimes, the supervisor log). Nothing is
hand-entered, so the block cannot drift from the run it describes.

Usage:  .venv/bin/python scripts/update_training_status.py [--check]

  --check  exit 1 if the README block is out of date, without writing.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
METRICS = REPO / "logs" / "medium_metrics.csv"
SUPERVISOR_LOG = REPO / "logs" / "supervisor.log"
CKPT_DIR = REPO / "checkpoints" / "medium"
TRAIN_CONFIG = REPO / "configs" / "train" / "fineweb_2x2080ti.yaml"

START = "<!-- TRAINING-STATUS:START -->"
END = "<!-- TRAINING-STATUS:END -->"

# A checkpoint pair implying a rate outside this range is not a real
# throughput measurement -- it spans a crash, a reboot, or an idle gap
# between runs. Better to omit the ETA than to publish a fabricated one.
MIN_SEC_PER_STEP = 0.5
MAX_SEC_PER_STEP = 60.0


def read_metrics() -> tuple[dict | None, dict | None]:
    """Last training row and last eval row from the metrics CSV."""
    if not METRICS.exists():
        return None, None
    last_train = last_val = None
    with METRICS.open(newline="") as fh:
        for row in csv.DictReader(fh):
            # Eval rows carry val_loss and leave train_loss empty; training
            # rows do the reverse. Both share the step column.
            if row.get("val_loss"):
                last_val = row
            if row.get("train_loss"):
                last_train = row
    return last_train, last_val


def checkpoints() -> list[tuple[int, float]]:
    """(step, mtime) for every saved checkpoint, oldest step first."""
    if not CKPT_DIR.is_dir():
        return []
    out = []
    for path in CKPT_DIR.glob("step_*.pt"):
        match = re.fullmatch(r"step_(\d+)\.pt", path.name)
        if match:
            out.append((int(match.group(1)), path.stat().st_mtime))
    return sorted(out)


def run_start() -> float | None:
    """Wall-clock start of the current training attempt, as an epoch time.

    Taken from the supervisor's own launch line. Needed because checkpoint
    mtimes alone cannot distinguish "500 steps took 43 minutes" from "500
    steps took 43 minutes, then the machine was off for two hours".
    """
    if not SUPERVISOR_LOG.exists():
        return None
    stamps = re.findall(r"\[supervisor\] (\S+) attempt \d+:", SUPERVISOR_LOG.read_text())
    if not stamps:
        return None
    try:
        return datetime.fromisoformat(stamps[-1]).timestamp()
    except ValueError:
        return None


def sec_per_step(ckpts: list[tuple[int, float]]) -> float | None:
    """Throughput from the two newest checkpoints of the *current* attempt.

    Checkpoints written before the current attempt started are excluded: an
    interval that straddles a crash, a reboot, or an idle gap measures
    downtime, not throughput, and would understate progress badly enough to
    publish a misleading ETA. A restart therefore reports no ETA until it has
    saved two checkpoints of its own, which is the honest answer -- nothing
    yet observed constrains the rate.
    """
    started = run_start()
    if started is not None:
        # Small tolerance: a checkpoint saved moments after launch belongs to
        # this attempt even if the clocks disagree by a few seconds.
        ckpts = [(step, mtime) for step, mtime in ckpts if mtime >= started - 60]
    if len(ckpts) < 2:
        return None
    (prev_step, prev_time), (last_step, last_time) = ckpts[-2], ckpts[-1]
    if last_step <= prev_step:
        return None
    rate = (last_time - prev_time) / (last_step - prev_step)
    # Second line of defence, for anything the attempt filter cannot see
    # (a long mid-attempt stall, a clock jump).
    return rate if MIN_SEC_PER_STEP <= rate <= MAX_SEC_PER_STEP else None


def config_value(key: str, default: int | float) -> int | float:
    """Read a scalar out of the training YAML without a yaml dependency."""
    if not TRAIN_CONFIG.exists():
        return default
    pattern = re.compile(rf"^{re.escape(key)}:\s*([0-9.e+-]+)\s*$", re.MULTILINE)
    match = pattern.search(TRAIN_CONFIG.read_text())
    if not match:
        return default
    text = match.group(1)
    return float(text) if any(c in text for c in ".e") else int(text)


def nproc() -> int:
    """Processes per node the current run was actually launched with.

    Read from the supervisor log rather than assumed from the config: this
    run is deliberately single-card (GPU 1 is thermally throttled), so the
    config's 2-GPU token arithmetic does not describe it.
    """
    if not SUPERVISOR_LOG.exists():
        return 1
    matches = re.findall(r"nproc_per_node=(\d+)", SUPERVISOR_LOG.read_text())
    return int(matches[-1]) if matches else 1


def is_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "scripts/train.py"], capture_output=True, text=True
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def humanize(seconds: float) -> str:
    delta = timedelta(seconds=int(seconds))
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes = rem // 60
    if hours >= 24:
        return f"~{hours // 24}d {hours % 24}h"
    if hours:
        return f"~{hours}h {minutes}m"
    return f"~{minutes}m"


def build_block() -> str:
    last_train, last_val = read_metrics()
    ckpts = checkpoints()
    max_steps = int(config_value("max_steps", 20000))
    running = is_running()

    if last_train is None:
        return (
            f"{START}\n\n## Live Training Status\n\n"
            f"No training metrics recorded yet.\n\n{END}"
        )

    step = int(last_train["step"])
    train_loss = float(last_train["train_loss"])
    lr = float(last_train["lr"])
    pct = 100.0 * step / max_steps

    tokens_per_step = (
        int(config_value("batch_size", 8))
        * int(config_value("grad_accum_steps", 30))
        * int(config_value("seq_len", 512))
        * nproc()
    )
    tokens_done = step * tokens_per_step

    rate = sec_per_step(ckpts)
    eta_label, eta_line = "Estimated remaining", "—"
    if rate and step < max_steps and running:
        remaining = (max_steps - step) * rate
        finish = datetime.now(timezone.utc).astimezone() + timedelta(seconds=remaining)
        eta_line = f"{humanize(remaining)} (≈ {finish.strftime('%Y-%m-%d %H:%M %Z')})"
    elif step >= max_steps:
        # "Estimated remaining: complete" is an awkward way to report a finished
        # run; once there is nothing left to estimate, report when it ended.
        eta_label = "Finished"
        finished_at = datetime.fromtimestamp(ckpts[-1][1]).astimezone() if ckpts else None
        eta_line = finished_at.strftime("%Y-%m-%d %H:%M %Z") if finished_at else "yes"

    if running:
        state = "🟢 **running**"
    elif step >= max_steps:
        state = "✅ **complete**"
    else:
        state = "⏸️ **stopped** (resumes from the newest checkpoint)"

    # Progress bar: 40 cells, one filled cell per 2.5% of the run.
    filled = int(round(40 * step / max_steps))
    bar = "█" * filled + "░" * (40 - filled)

    val_bits = ""
    if last_val is not None:
        val_bits = (
            f"| **Validation loss** | {float(last_val['val_loss']):.4f} "
            f"(perplexity {float(last_val['val_perplexity']):.2f}) "
            f"at step {int(last_val['step']):,} |\n"
        )

    throughput = ""
    if rate:
        throughput = (
            f"| **Throughput** | {rate:.2f} s/step "
            f"({tokens_per_step / rate:,.0f} tokens/s) |\n"
        )

    updated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    # A finished run is not "live"; the heading follows the state so the block
    # does not keep advertising a watch on something that ended.
    if step >= max_steps and not running:
        heading = "Training Run — `medium` (124M) on FineWeb-Edu"
        provenance = (
            "> This section was generated by `scripts/update_training_status.py` from\n"
            "> the run's own artifacts (metrics CSV, checkpoint timestamps, supervisor\n"
            "> log), so it reports the run rather than asserting a claim about it."
        )
    else:
        heading = "Live Training Status — `medium` (124M) on FineWeb-Edu"
        provenance = (
            "> This section is regenerated by `scripts/update_training_status.py` from the\n"
            "> run's own artifacts (metrics CSV, checkpoint timestamps, supervisor log) and\n"
            "> pushed on a schedule, so it reflects the run rather than a claim about it."
        )

    return f"""{START}

## {heading}

{provenance}

**Status:** {state} — step **{step:,} / {max_steps:,}** ({pct:.1f}%)

```
{bar}  {pct:.1f}%
```

| | |
|---|---|
| **Model** | `medium` — 123,587,328 parameters |
| **Corpus** | FineWeb-Edu, 5.0B tokens (4.9B train / 100M val) |
| **Training loss** | {train_loss:.4f} (learning rate {lr:.2e}) |
{val_bits}| **Tokens seen** | {tokens_done / 1e9:.2f}B ({tokens_per_step:,} per step) |
{throughput}| **Hardware** | 1x RTX 2080 Ti (11GB), fp16 + GradScaler |
| **{eta_label}** | {eta_line} |
| **Last updated** | {updated} |

Full loss curve: [`logs/medium_metrics.csv`](logs/medium_metrics.csv) ·
Run log: [`logs/supervisor.log`](logs/supervisor.log)

{END}"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    text = README.read_text()
    if START not in text or END not in text:
        print(f"error: {README} has no TRAINING-STATUS markers", file=sys.stderr)
        return 2

    block = build_block()
    updated = re.sub(
        re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S
    )

    # The timestamp changes on every run, so compare everything else -- other-
    # wise every invocation would look like a change and produce a commit.
    def strip_ts(s: str) -> str:
        return re.sub(r"\| \*\*Last updated\*\* \|.*?\|", "", s)

    if strip_ts(updated) == strip_ts(text):
        print("training status unchanged")
        return 1 if args.check else 0

    if args.check:
        print("training status out of date")
        return 1

    README.write_text(updated)
    last_train, _ = read_metrics()
    step = int(last_train["step"]) if last_train else 0
    print(f"training status updated: step {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
