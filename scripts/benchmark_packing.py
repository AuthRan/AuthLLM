"""Benchmark: what sequence packing actually buys on a real fine-tuning step.

The claim packing makes is not "fewer windows" -- that is arithmetic, and
`PackedInstructionDataset.packing_efficiency` reports it without touching a
GPU. The claim worth checking is that filling the window costs no more per
step than padding it did, so the extra supervised tokens are close to free.

That is not obvious. Padding is skipped by the loss but not by the arithmetic:
the forward and backward pass both process all `seq_len` positions either way,
so a packed step should cost the same. But packing also swaps the plain causal
mask -- a single (seq_len, seq_len) matrix shared by the whole batch -- for a
per-example block-diagonal one of shape (batch, 1, seq_len, seq_len), and that
larger mask has to be built, materialized in the autocast dtype, and broadcast
against every head. This script measures whether that shows up.

Reports, for packed and unpacked at identical batch shape: milliseconds per
optimizer step, supervised (unmasked) tokens per step, supervised tokens per
second, and peak allocated memory.

Usage:
    python scripts/benchmark_packing.py --data data/sft/alpaca.jsonl
    python scripts/benchmark_packing.py --data data/sft/dolly.jsonl --steps 20
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ashugpt.config import load_model_config, load_train_config
from ashugpt.data.instruction import (
    IGNORE_INDEX,
    InstructionDataset,
    InstructionExample,
    PackedInstructionDataset,
)
from ashugpt.model.gpt import AshuGPT
from ashugpt.training.amp import autocast_context, build_grad_scaler, resolve_amp_dtype


def read_jsonl(path: Path) -> list[InstructionExample]:
    examples = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        examples.append(InstructionExample(row["instruction"], row.get("input", ""), row["output"]))
    return examples


def benchmark(model, dataset, packed: bool, config, device, amp_dtype, steps: int, warmup: int) -> dict:
    """One optimizer step per iteration, timed after `warmup` untimed ones.

    The warmup matters more than usual here: the first CUDA step pays kernel
    autotuning and allocator growth that would otherwise land entirely in the
    first scenario measured and make it look slower than it is.
    """
    loader = DataLoader(dataset, batch_size=config.batch_size, shuffle=True, drop_last=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.max_lr)
    scaler = build_grad_scaler(device.type, amp_dtype)

    torch.cuda.reset_peak_memory_stats() if device.type == "cuda" else None
    batches = iter(loader)
    supervised = 0
    start = 0.0

    for i in range(warmup + steps):
        if i == warmup:
            if device.type == "cuda":
                torch.cuda.synchronize()
            start = time.time()
            supervised = 0

        try:
            batch = next(batches)
        except StopIteration:
            batches = iter(loader)
            batch = next(batches)

        moved = [t.to(device) for t in batch]
        input_ids, labels = moved[0], moved[1]
        extras = {"segment_ids": moved[2], "position_ids": moved[3]} if packed else {}

        optimizer.zero_grad(set_to_none=True)
        with autocast_context(device.type, amp_dtype):
            output = model(input_ids, labels=labels, **extras)
        scaler.scale(output.loss).backward()
        scaler.step(optimizer)
        scaler.update()

        if i >= warmup:
            supervised += (labels != IGNORE_INDEX).sum().item()

    if device.type == "cuda":
        torch.cuda.synchronize()
    elapsed = time.time() - start

    return {
        "ms_per_step": elapsed / steps * 1000,
        "supervised_per_step": supervised / steps,
        "supervised_per_second": supervised / elapsed,
        "peak_gb": (torch.cuda.max_memory_allocated() / 1e9) if device.type == "cuda" else float("nan"),
        "windows": len(dataset),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="JSONL from scripts/prepare_instruction_data.py")
    parser.add_argument("--model", type=Path, default=Path("configs/model/medium.yaml"))
    parser.add_argument("--train", type=Path, default=Path("configs/train/sft_alpaca.yaml"))
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=3)
    args = parser.parse_args()

    model_config = load_model_config(args.model)
    train_config = load_train_config(args.train)

    from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer

    tokenizer = TiktokenBPETokenizer()
    examples = read_jsonl(args.data)
    random.Random(train_config.seed).shuffle(examples)

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    amp_dtype = resolve_amp_dtype(train_config.amp_dtype)

    torch.manual_seed(train_config.seed)
    model = AshuGPT(model_config).to(device)
    model.set_memory_optimizations(efficient_attention=train_config.use_efficient_attention)
    model.train()

    unpacked = InstructionDataset(examples, tokenizer, seq_len=train_config.seq_len)
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=train_config.seq_len)

    print(f"{args.data.name}: {len(unpacked):,} examples -> {len(packed):,} packed windows "
          f"({len(unpacked) / len(packed):.2f}x fewer, {packed.packing_efficiency:.1%} of positions used)")
    print(f"batch {train_config.batch_size} x seq {train_config.seq_len}, "
          f"amp {train_config.amp_dtype}, efficient_attention {train_config.use_efficient_attention}, "
          f"device {device}\n")

    results = {
        "unpacked": benchmark(model, unpacked, False, train_config, device, amp_dtype, args.steps, args.warmup),
        "packed": benchmark(model, packed, True, train_config, device, amp_dtype, args.steps, args.warmup),
    }

    header = f"{'':10} {'ms/step':>9} {'sup tok/step':>13} {'sup tok/s':>11} {'peak GB':>9}"
    print(header)
    print("-" * len(header))
    for name, r in results.items():
        print(f"{name:10} {r['ms_per_step']:9.0f} {r['supervised_per_step']:13.0f} "
              f"{r['supervised_per_second']:11.0f} {r['peak_gb']:9.2f}")

    speedup = results["packed"]["supervised_per_second"] / results["unpacked"]["supervised_per_second"]
    step_cost = results["packed"]["ms_per_step"] / results["unpacked"]["ms_per_step"]
    print(f"\nsupervised throughput: {speedup:.2f}x     per-step cost: {step_cost:.2f}x")


if __name__ == "__main__":
    main()
