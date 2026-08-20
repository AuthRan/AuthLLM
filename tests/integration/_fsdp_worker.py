"""Standalone script (not collected by pytest -- leading underscore) that
runs FSDP training steps across two processes, launched from
tests/integration/test_fsdp.py.

Deliberately mirrors _ddp_worker.py's scenario so the two parallelism
strategies can be compared against the *same* single-process baseline: the
claim FSDP makes is that sharding changes where the arithmetic happens,
not what it computes.

Runs on CPU with the gloo backend. FSDP2 (`fully_shard`) is what makes that
possible at all -- FSDP1 raises in `_init_device_handle` before any
wrapping when there is no accelerator, so a CPU parity test could not exist
against it.

Not meant to be run with plain `python` -- it needs RANK/WORLD_SIZE/etc. in
the environment, which only a distributed launcher sets.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import torch

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.data.dataset import TokenizedDataset
from ashugpt.model.gpt import AshuGPT
from ashugpt.training.trainer import train

SEED = 0
MODEL_CONFIG = ModelConfig(
    name="fsdp-smoke-test",
    vocab_size=32,
    d_model=16,
    n_layers=2,
    n_heads=2,
    n_kv_heads=2,
    d_ff=32,
    context_length=16,
)
SEQ_LEN = 4
NUM_EXAMPLES = 8  # divisible by world_size=2 -- no DistributedSampler padding/duplication


def build_dataset() -> TokenizedDataset:
    token_ids = torch.arange(NUM_EXAMPLES + SEQ_LEN) % MODEL_CONFIG.vocab_size
    return TokenizedDataset(token_ids, seq_len=SEQ_LEN)


def build_train_config(batch_size: int, max_steps: int = 1, checkpoint_interval: int = 1) -> TrainConfig:
    return TrainConfig(
        batch_size=batch_size,
        seq_len=SEQ_LEN,
        max_steps=max_steps,
        warmup_steps=1,
        max_lr=1e-2,
        min_lr=1e-3,
        weight_decay=0.0,
        log_interval=1,
        eval_interval=10**9,
        checkpoint_interval=checkpoint_interval,
        amp_dtype="none",  # no rounding noise in a cross-process comparison
        seed=SEED,
        parallel="fsdp",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=1)
    parser.add_argument("--validate", action="store_true", help="Exercise the validation path (a collective here)")
    args = parser.parse_args()

    rank = int(os.environ["RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    per_rank_batch_size = NUM_EXAMPLES // world_size

    torch.manual_seed(SEED)
    model = AshuGPT(MODEL_CONFIG)

    dataset = build_dataset()
    train_config = build_train_config(per_rank_batch_size, max_steps=args.max_steps)
    if args.validate:
        train_config.eval_interval = 1

    train(
        model,
        dataset,
        val_dataset=dataset if args.validate else None,
        config=train_config,
        model_config=MODEL_CONFIG,
        checkpoint_dir=args.output_dir / "checkpoints",
    )

    # Save this rank's *local shard*, not a gathered copy. Gathering is a
    # collective, and train() has already destroyed the process group by
    # the time it returns -- but more usefully, the shards are the thing
    # worth recording: the test reassembles them and checks they are
    # complementary halves of the model the checkpoint holds, which is how
    # you tell real sharding from FSDP quietly replicating.
    shards = {
        name: (p.to_local() if hasattr(p, "to_local") else p).detach().cpu().clone()
        for name, p in model.named_parameters()
    }
    torch.save(shards, args.output_dir / f"rank{rank}_shard.pt")


if __name__ == "__main__":
    main()
