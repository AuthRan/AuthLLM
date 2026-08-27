"""Block until every GPU is idle enough to start a sweep on.

paper/NIGHT-PLAN.md recorded this preflight as the fix for an OOM cascade that
burned a whole grid in 0.9 minutes: a sweep started before the previous one had
released its memory, its first run OOMed, and because a failed run immediately
starts the next, one busy card turned into fifteen consecutive failures and an
empty ledger. The circuit breaker in `sweep_lr_packing.py` stops the cascade;
this stops it from starting.

The margin is genuinely thin. A 124M fine-tune needs ~8.6 GB of an 11.26 GB
card, and the desktop and the inference server hold ~0.9 GB of GPU 0, so a
lingering training process is enough to lose the grid.

    python scripts/wait_for_gpus.py                  # both cards, 1.5 GB, 2h
    python scripts/wait_for_gpus.py --gpus 0
    python scripts/wait_for_gpus.py --threshold-mb 2000 --timeout 600

Exit 0 when the cards are free, 1 on timeout, so it composes:

    python scripts/wait_for_gpus.py && python scripts/sweep_lr_packing.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time


def used_mb(gpus: list[int]) -> dict[int, int] | None:
    """Memory in use per GPU, or None if nvidia-smi could not be read."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    usage = {}
    for line in out.strip().split("\n"):
        index, memory = (part.strip() for part in line.split(","))
        if int(index) in gpus:
            usage[int(index)] = int(memory)
    return usage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gpus", nargs="+", type=int, default=[0, 1])
    # 1.5 GB clears the desktop and the inference server without clearing a
    # training process, which is the distinction that matters.
    parser.add_argument("--threshold-mb", type=int, default=1500)
    parser.add_argument("--timeout", type=int, default=7200, help="seconds; 0 waits forever")
    parser.add_argument("--poll", type=int, default=30, help="seconds between checks")
    args = parser.parse_args()

    started = time.monotonic()
    while True:
        usage = used_mb(args.gpus)
        if usage is None:
            # No nvidia-smi is not a reason to block a CPU-only machine forever.
            print("wait_for_gpus: nvidia-smi unavailable, not waiting", flush=True)
            return
        busy = {gpu: mb for gpu, mb in usage.items() if mb > args.threshold_mb}
        if not busy:
            report = ", ".join(f"gpu {gpu} {mb} MiB" for gpu, mb in sorted(usage.items()))
            print(f"wait_for_gpus: clear ({report})", flush=True)
            return

        waited = time.monotonic() - started
        if args.timeout and waited > args.timeout:
            report = ", ".join(f"gpu {gpu} {mb} MiB" for gpu, mb in sorted(busy.items()))
            print(f"wait_for_gpus: still busy after {waited / 60:.0f} min ({report})", flush=True)
            sys.exit(1)

        report = ", ".join(f"gpu {gpu} {mb} MiB" for gpu, mb in sorted(busy.items()))
        print(f"wait_for_gpus: waiting on {report} ({waited / 60:.0f} min)", flush=True)
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
