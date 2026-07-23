"""Benchmark: peak memory and per-step wall-clock time for each Milestone 9
memory optimization, on vs. off.

There's no GPU in this environment, so "memory" here means process-level
resident memory (RSS, via psutil) rather than torch.cuda's per-tensor
allocator stats. Each scenario runs in its own fresh subprocess -- CPU
tensor memory freed mid-run often stays cached in PyTorch's allocator
rather than returned to the OS, which would otherwise let one scenario's
memory bleed into the next scenario's "before" reading.

Usage:
    python scripts/benchmark_memory.py                      # run every scenario, print a summary table
    python scripts/benchmark_memory.py --scenario baseline  # run one scenario directly (also used internally)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil
import torch

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.data.dataset import TokenizedDataset
from ashugpt.model.gpt import AshuGPT
from ashugpt.training.trainer import train

# A deliberately mid-sized model + long-ish sequences: big enough that
# activation memory (what gradient checkpointing targets) is clearly
# visible against the model's own parameter memory. vocab_size is kept
# small on purpose so the embedding table doesn't dominate and drown out
# the effect being measured.
#
# seq_len=256 (not longer) and NUM_STEPS=1 are deliberately small: this
# environment's CPU has no native bf16 compute support, so the
# mixed_precision/all_combined scenarios below run bf16 in a slow emulated
# path (~50x slower than fp32 here -- a real, reportable finding in its
# own right, not a benchmark bug). Bigger settings would make every
# bf16 scenario take minutes; this keeps the whole sweep around 3-5 minutes.
BENCHMARK_MODEL_CONFIG = ModelConfig(
    name="benchmark", vocab_size=1000, d_model=256, n_layers=6, n_heads=8, n_kv_heads=8, d_ff=768, context_length=1024
)
SEQ_LEN = 256
NUM_STEPS = 1


@dataclass
class Scenario:
    name: str
    description: str
    batch_size: int = 8
    grad_accum_steps: int = 1
    amp_dtype: str = "none"
    gradient_checkpointing: bool = False
    use_efficient_attention: bool = False
    optimizer: str = "adamw"


SCENARIOS = [
    Scenario("baseline", "everything off -- fp32, no checkpointing, manual attention, AdamW"),
    Scenario("gradient_checkpointing", "+ gradient checkpointing", gradient_checkpointing=True),
    Scenario("mixed_precision", "+ bf16 autocast", amp_dtype="bfloat16"),
    Scenario(
        "grad_accum_x8",
        "same *effective* batch (8) via batch_size=1 x 8 accum steps, instead of batch_size=8 x 1",
        batch_size=1,
        grad_accum_steps=8,
    ),
    Scenario("efficient_attention", "+ fused scaled_dot_product_attention", use_efficient_attention=True),
    Scenario("sgd_optimizer", "AdamW -> SGD+momentum (1 state buffer/param instead of 2)", optimizer="sgd"),
    Scenario(
        "all_combined",
        "checkpointing + bf16 + grad_accum_x8 + efficient attention, AdamW",
        batch_size=1,
        grad_accum_steps=8,
        amp_dtype="bfloat16",
        gradient_checkpointing=True,
        use_efficient_attention=True,
    ),
]


def _train_config_for(scenario: Scenario) -> TrainConfig:
    return TrainConfig(
        batch_size=scenario.batch_size,
        seq_len=SEQ_LEN,
        max_steps=NUM_STEPS,
        warmup_steps=1,
        max_lr=1e-4,
        min_lr=1e-5,
        grad_accum_steps=scenario.grad_accum_steps,
        log_interval=NUM_STEPS,  # keep worker stdout quiet -- only the final RESULT line matters
        eval_interval=10**9,
        checkpoint_interval=10**9,
        amp_dtype=scenario.amp_dtype,
        gradient_checkpointing=scenario.gradient_checkpointing,
        use_efficient_attention=scenario.use_efficient_attention,
        optimizer=scenario.optimizer,
        seed=0,
    )


def _run_training(scenario: Scenario) -> None:
    torch.manual_seed(0)
    model = AshuGPT(BENCHMARK_MODEL_CONFIG)
    train_config = _train_config_for(scenario)

    num_tokens_needed = scenario.batch_size * scenario.grad_accum_steps * NUM_STEPS * 2 + SEQ_LEN + 1
    token_ids = torch.randint(0, BENCHMARK_MODEL_CONFIG.vocab_size, (num_tokens_needed,))
    dataset = TokenizedDataset(token_ids, seq_len=SEQ_LEN)

    train(model, dataset, val_dataset=None, config=train_config)


def _measure_peak_rss(fn, poll_interval: float = 0.02) -> tuple[int, float]:
    """Runs fn() while polling this process's RSS from a background
    thread, tracking the max seen. Returns (peak_rss_bytes, elapsed_seconds)."""
    process = psutil.Process(os.getpid())
    peak = [process.memory_info().rss]
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], process.memory_info().rss)
            time.sleep(poll_interval)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    start = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - start
    stop.set()
    poller.join()
    peak[0] = max(peak[0], process.memory_info().rss)
    return peak[0], elapsed


def _run_worker(scenario_name: str) -> None:
    scenario = next(s for s in SCENARIOS if s.name == scenario_name)
    peak_bytes, elapsed = _measure_peak_rss(lambda: _run_training(scenario))
    per_step = elapsed / NUM_STEPS
    # Machine-parseable line the orchestrator greps out of this
    # subprocess's stdout; everything else printed above it (training
    # logs) is ignored.
    print(f"RESULT peak_rss_mb={peak_bytes / 1e6:.1f} elapsed_s={elapsed:.2f} per_step_s={per_step:.3f}")


def _parse_result_line(stdout: str) -> dict[str, float]:
    line = next(line for line in stdout.splitlines() if line.startswith("RESULT"))
    fields = {}
    for token in line.split()[1:]:
        key, value = token.split("=")
        fields[key] = float(value)
    return fields


def _run_orchestrator() -> None:
    script = Path(__file__).resolve()
    rows = []
    print(f"Benchmark model: {BENCHMARK_MODEL_CONFIG.name} "
          f"({AshuGPT(BENCHMARK_MODEL_CONFIG).num_parameters():,} params), "
          f"seq_len={SEQ_LEN}, {NUM_STEPS} steps/scenario\n")

    for scenario in SCENARIOS:
        print(f"Running: {scenario.name} ({scenario.description}) ...")
        result = subprocess.run(
            [sys.executable, str(script), "--scenario", scenario.name],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            print(f"  FAILED:\n{result.stderr[-2000:]}")
            continue
        rows.append((scenario, _parse_result_line(result.stdout)))

    baseline = next((r for s, r in rows if s.name == "baseline"), None)

    print(f"\n{'scenario':<24}{'peak RSS (MB)':>16}{'vs baseline':>14}{'s/step':>10}")
    print("-" * 64)
    for scenario, result in rows:
        rss_delta = ""
        if baseline is not None and scenario.name != "baseline":
            pct = (result["peak_rss_mb"] - baseline["peak_rss_mb"]) / baseline["peak_rss_mb"] * 100
            rss_delta = f"{pct:+.1f}%"
        print(f"{scenario.name:<24}{result['peak_rss_mb']:>16.1f}{rss_delta:>14}{result['per_step_s']:>10.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=[s.name for s in SCENARIOS], default=None)
    args = parser.parse_args()

    if args.scenario is not None:
        _run_worker(args.scenario)
    else:
        _run_orchestrator()


if __name__ == "__main__":
    main()
