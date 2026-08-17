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

# On GPU the CPU-era settings above measure almost nothing: a 5.4M-param model
# at seq_len=256 barely registers against fixed CUDA context overhead, and a
# single step is dominated by kernel launch latency. The GPU sweep therefore
# uses a bigger model, longer sequences, and more steps -- affordable precisely
# because the hardware is no longer the bottleneck.
CUDA_MODEL_CONFIG = ModelConfig(
    name="benchmark-cuda",
    vocab_size=50304,
    d_model=768,
    n_layers=12,
    n_heads=12,
    n_kv_heads=12,
    d_ff=2048,
    context_length=1024,
)
CUDA_SEQ_LEN = 512
CUDA_NUM_STEPS = 6


def resolve_device(requested: str) -> str:
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return requested


def has_native_bf16() -> bool:
    """True only for real bf16 tensor cores (compute capability >= 8.0).

    Deliberately not torch.cuda.is_bf16_supported(), which returns True on
    Turing by counting emulation -- the exact trap documented in
    ashugpt/training/amp.py.
    """
    return torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8


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

# The GPU sweep runs both fp16 and bf16 as separate scenarios rather than one
# "mixed_precision" row. On hardware without native bf16 they are not
# interchangeable -- and the size of that gap is the point being measured, so
# it belongs in the table rather than in a footnote.
CUDA_SCENARIOS = [
    Scenario("baseline", "everything off -- fp32, no checkpointing, manual attention, AdamW"),
    Scenario("gradient_checkpointing", "+ gradient checkpointing", gradient_checkpointing=True),
    Scenario("mixed_precision_fp16", "+ fp16 autocast (+ GradScaler)", amp_dtype="float16"),
    Scenario("mixed_precision_bf16", "+ bf16 autocast", amp_dtype="bfloat16"),
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
        "checkpointing + fp16 + grad_accum_x8 + efficient attention, AdamW",
        batch_size=1,
        grad_accum_steps=8,
        amp_dtype="float16",
        gradient_checkpointing=True,
        use_efficient_attention=True,
    ),
]


def scenarios_for(device: str) -> list[Scenario]:
    return CUDA_SCENARIOS if device == "cuda" else SCENARIOS


def _train_config_for(scenario: Scenario, device: str) -> TrainConfig:
    seq_len = CUDA_SEQ_LEN if device == "cuda" else SEQ_LEN
    num_steps = CUDA_NUM_STEPS if device == "cuda" else NUM_STEPS
    return TrainConfig(
        batch_size=scenario.batch_size,
        seq_len=seq_len,
        max_steps=num_steps,
        warmup_steps=1,
        max_lr=1e-4,
        min_lr=1e-5,
        grad_accum_steps=scenario.grad_accum_steps,
        log_interval=num_steps,  # keep worker stdout quiet -- only the final RESULT line matters
        eval_interval=10**9,
        checkpoint_interval=10**9,
        amp_dtype=scenario.amp_dtype,
        gradient_checkpointing=scenario.gradient_checkpointing,
        use_efficient_attention=scenario.use_efficient_attention,
        optimizer=scenario.optimizer,
        seed=0,
    )


def _run_training(scenario: Scenario, device: str) -> None:
    model_config = CUDA_MODEL_CONFIG if device == "cuda" else BENCHMARK_MODEL_CONFIG
    seq_len = CUDA_SEQ_LEN if device == "cuda" else SEQ_LEN
    num_steps = CUDA_NUM_STEPS if device == "cuda" else NUM_STEPS

    torch.manual_seed(0)
    model = AshuGPT(model_config)
    train_config = _train_config_for(scenario, device)

    num_tokens_needed = scenario.batch_size * scenario.grad_accum_steps * num_steps * 2 + seq_len + 1
    token_ids = torch.randint(0, model_config.vocab_size, (num_tokens_needed,))
    dataset = TokenizedDataset(token_ids, seq_len=seq_len)

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


def _measure_peak_cuda(fn) -> tuple[int, float]:
    """CUDA counterpart to _measure_peak_rss.

    torch.cuda.max_memory_allocated() is strictly better than the RSS polling
    used on CPU: it is the allocator's own exact high-water mark for tensor
    memory, not a sampled process-level number that also includes the Python
    interpreter, loaded libraries, and whatever the allocator has cached but
    not returned. That is why the CPU-era numbers needed a "roughly a third of
    the real number" calibration note and these do not.

    synchronize() before stopping the clock matters: CUDA kernels are launched
    asynchronously, so without it the timing would measure how fast Python
    enqueued the work, not how long the GPU took to do it.
    """
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    start = time.perf_counter()
    fn()
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    return torch.cuda.max_memory_allocated(), elapsed


def _run_worker(scenario_name: str, device: str) -> None:
    scenario = next(s for s in scenarios_for(device) if s.name == scenario_name)
    num_steps = CUDA_NUM_STEPS if device == "cuda" else NUM_STEPS

    if device == "cuda":
        peak_bytes, elapsed = _measure_peak_cuda(lambda: _run_training(scenario, device))
        metric = "peak_vram_mb"
    else:
        peak_bytes, elapsed = _measure_peak_rss(lambda: _run_training(scenario, device))
        metric = "peak_rss_mb"

    per_step = elapsed / num_steps
    # Machine-parseable line the orchestrator greps out of this
    # subprocess's stdout; everything else printed above it (training
    # logs) is ignored.
    print(f"RESULT {metric}={peak_bytes / 1e6:.1f} elapsed_s={elapsed:.2f} per_step_s={per_step:.3f}")


def _parse_result_line(stdout: str) -> dict[str, float]:
    line = next(line for line in stdout.splitlines() if line.startswith("RESULT"))
    fields = {}
    for token in line.split()[1:]:
        key, value = token.split("=")
        fields[key] = float(value)
    return fields


def _run_orchestrator(device: str) -> None:
    script = Path(__file__).resolve()
    rows = []

    model_config = CUDA_MODEL_CONFIG if device == "cuda" else BENCHMARK_MODEL_CONFIG
    seq_len = CUDA_SEQ_LEN if device == "cuda" else SEQ_LEN
    num_steps = CUDA_NUM_STEPS if device == "cuda" else NUM_STEPS
    metric = "peak_vram_mb" if device == "cuda" else "peak_rss_mb"
    metric_label = "peak VRAM (MB)" if device == "cuda" else "peak RSS (MB)"

    where = torch.cuda.get_device_name() if device == "cuda" else "CPU"
    print(f"Benchmark model: {model_config.name} "
          f"({AshuGPT(model_config).num_parameters():,} params), "
          f"seq_len={seq_len}, {num_steps} steps/scenario, device={device} ({where})")
    if device == "cuda" and not has_native_bf16():
        print("NOTE: this GPU has no native bf16 (compute capability < 8.0) -- bf16 rows below are emulated.")
    print()

    for scenario in scenarios_for(device):
        print(f"Running: {scenario.name} ({scenario.description}) ...")
        env = dict(os.environ)
        if device == "cpu":
            # Force the scenario onto CPU even on a CUDA box: the trainer picks
            # its device from torch.cuda.is_available(), so hiding the GPUs is
            # what makes --device cpu meaningful here.
            env["CUDA_VISIBLE_DEVICES"] = ""
        result = subprocess.run(
            [sys.executable, str(script), "--scenario", scenario.name, "--device", device],
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
        )
        if result.returncode != 0:
            print(f"  FAILED:\n{result.stderr[-2000:]}")
            continue
        rows.append((scenario, _parse_result_line(result.stdout)))

    baseline = next((r for s, r in rows if s.name == "baseline"), None)

    print(f"\n{'scenario':<24}{metric_label:>16}{'vs baseline':>14}{'s/step':>10}")
    print("-" * 64)
    for scenario, result in rows:
        delta = ""
        if baseline is not None and scenario.name != "baseline":
            pct = (result[metric] - baseline[metric]) / baseline[metric] * 100
            delta = f"{pct:+.1f}%"
        print(f"{scenario.name:<24}{result[metric]:>16.1f}{delta:>14}{result['per_step_s']:>10.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default=None)
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="auto (default) uses CUDA when available. The GPU sweep uses a larger model and "
        "reports exact allocator peak VRAM instead of sampled process RSS.",
    )
    args = parser.parse_args()
    device = resolve_device(args.device)

    if device == "cuda" and not torch.cuda.is_available():
        parser.error("--device cuda requested but no CUDA device is available")

    if args.scenario is not None:
        valid = {s.name for s in scenarios_for(device)}
        if args.scenario not in valid:
            parser.error(f"unknown scenario {args.scenario!r} for device {device}; choose from {sorted(valid)}")
        _run_worker(args.scenario, device)
    else:
        _run_orchestrator(device)


if __name__ == "__main__":
    main()
