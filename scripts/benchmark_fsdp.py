"""Benchmark: what sharding buys, and what it costs, on real hardware.

DDP and FSDP answer different questions. DDP asks "how fast can I train a
model that fits?"; FSDP asks "can I train a model that doesn't?". So the
number that matters here is not throughput, it is **peak memory per GPU**,
and the interesting row is the one where DDP has no number at all because
it ran out of memory.

For each requested model config and strategy, this runs a few real
optimizer steps and reports:

    peak GB/GPU     torch.cuda.max_memory_allocated, per rank
    reserved GB     what the caching allocator held, which is what
                    actually has to fit in the card
    s/step          wall clock, after a warmup step

Run it under torchrun, or with the manual env vars the tests use:

    torchrun --nproc_per_node=2 scripts/benchmark_fsdp.py --model medium --strategy ddp
    torchrun --nproc_per_node=2 scripts/benchmark_fsdp.py --model xl_1b --strategy fsdp
    torchrun --nproc_per_node=2 scripts/benchmark_fsdp.py --model xl_1b --strategy fsdp --cpu-offload

An out-of-memory failure is a *result*, not a crash: it is caught and
reported as OOM with the configuration that produced it, because "this
does not fit" is exactly what the table is trying to establish.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import torch

from ashugpt.config import load_model_config
from ashugpt.model.gpt import AshuGPT
from ashugpt.training.ddp import cleanup_distributed, setup_distributed, wrap_model_for_ddp
from ashugpt.training.fsdp import place_buffers_on_device, wrap_model_for_fsdp

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="medium", help="Name under configs/model/ (without .yaml)")
    parser.add_argument("--strategy", choices=["ddp", "fsdp"], default="fsdp")
    parser.add_argument("--cpu-offload", action="store_true", help="FSDP only: keep shards in host RAM")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--seq-len", type=int, default=512)
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument("--gradient-checkpointing", action="store_true")
    args = parser.parse_args()

    info = setup_distributed()
    try:
        model_config = load_model_config(REPO_ROOT / "configs" / "model" / f"{args.model}.yaml")

        # Build on the meta device: a 1.23B-parameter model is 4.9GB of fp32
        # that would otherwise be allocated in full, on every rank, before
        # sharding has a chance to split it -- which defeats the point on a
        # machine whose whole problem is that the model does not fit.
        with torch.device("meta"):
            model = AshuGPT(model_config)

        if args.strategy == "fsdp":
            model = wrap_model_for_fsdp(model, info, cpu_offload=args.cpu_offload)
            model.to_empty(device="cpu" if args.cpu_offload else info.device)
        else:
            model = model.to_empty(device=info.device)
            model = wrap_model_for_ddp(model, info)

        # to_empty() leaves uninitialized memory; a real run would load a
        # checkpoint or run the model's own init. Neither matters for a
        # memory measurement, but NaNs propagating into the optimizer would
        # make the step times meaningless, so give it finite values.
        raw = model.module if hasattr(model, "module") else model
        with torch.no_grad():
            for parameter in raw.parameters():
                parameter.normal_(std=0.02)
        place_buffers_on_device(raw, info.device)
        raw.set_memory_optimizations(
            gradient_checkpointing=args.gradient_checkpointing, efficient_attention=True
        )

        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5)
        tokens = torch.randint(
            0, model_config.vocab_size, (args.batch_size, args.seq_len), device=info.device
        )

        torch.cuda.reset_peak_memory_stats()
        elapsed = 0.0
        for step in range(args.steps + 1):
            start = time.perf_counter()
            output = model(tokens, labels=tokens)
            output.loss.backward()
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            torch.cuda.synchronize()
            if step > 0:  # step 0 is warmup: allocator growth and NCCL buffer setup
                elapsed += time.perf_counter() - start

        if info.is_main_process:
            offload = " +cpu-offload" if args.cpu_offload else ""
            print(
                f"{args.model:8s} {args.strategy}{offload:12s} "
                f"world={info.world_size} bs={args.batch_size} seq={args.seq_len} "
                f"gc={int(args.gradient_checkpointing)} | "
                f"peak {torch.cuda.max_memory_allocated() / 2**30:5.2f} GB/GPU | "
                f"reserved {torch.cuda.max_memory_reserved() / 2**30:5.2f} GB | "
                f"{elapsed / args.steps:6.2f} s/step"
            )
    except (torch.cuda.OutOfMemoryError, RuntimeError) as error:
        if not isinstance(error, torch.cuda.OutOfMemoryError) and "out of memory" not in str(error).lower():
            raise
        offload = " +cpu-offload" if args.cpu_offload else ""
        # Printed from *every* rank, not just rank 0. Which rank runs out
        # of memory first is not fixed, and a report only rank 0 can make
        # is a report you do not get on exactly the runs that matter.
        print(
            f"[rank{info.rank}] {args.model:8s} {args.strategy}{offload:12s} "
            f"world={info.world_size} bs={args.batch_size} seq={args.seq_len} "
            f"gc={int(args.gradient_checkpointing)} | OOM -- does not fit",
            flush=True,
        )
        # Exit hard rather than unwinding. An OOM on one rank is not a
        # local event: the other ranks are inside a collective waiting for
        # this one, and a tidy destroy_process_group() here leaves them
        # waiting on a peer that has politely stopped participating. The
        # run then *hangs* instead of failing -- which is how a benchmark
        # of what does not fit becomes a benchmark of nothing at all, and
        # is exactly what the first version of this script did. Killing
        # the process closes the socket so the peers abort with an error.
        os._exit(1)
    finally:
        cleanup_distributed(info)


if __name__ == "__main__":
    main()
