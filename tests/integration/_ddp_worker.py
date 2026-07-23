"""Standalone script (not collected by pytest -- leading underscore) that
runs one DDP training step, launched via torchrun/torch.distributed.run
from tests/integration/test_ddp.py. Every rank saves its own post-step
weights to <output-dir>/rank<N>_weights.pt so the launching test can load
and compare them.

Not meant to be run directly with plain `python` -- it needs RANK/
WORLD_SIZE/etc. in the environment, which only a distributed launcher sets.
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

# Fixed, small, deterministic scenario -- see test_ddp.py for the mathematical
# argument this setup is chosen to support (equal per-rank shard sizes let a
# single-process run over the *whole* dataset serve as an exact baseline).
SEED = 0
MODEL_CONFIG = ModelConfig(
    name="ddp-smoke-test",
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


def build_train_config(batch_size: int, checkpoint_interval: int) -> TrainConfig:
    return TrainConfig(
        batch_size=batch_size,
        seq_len=SEQ_LEN,
        max_steps=1,
        warmup_steps=1,
        max_lr=1e-2,
        min_lr=1e-3,
        weight_decay=0.0,
        log_interval=1,
        eval_interval=10**9,  # no validation set in this scenario
        checkpoint_interval=checkpoint_interval,
        amp_dtype="none",  # avoid bf16 rounding noise in the cross-process comparison
        seed=SEED,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rank = int(os.environ["RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    per_rank_batch_size = NUM_EXAMPLES // world_size

    torch.manual_seed(SEED)
    model = AshuGPT(MODEL_CONFIG)

    train_config = build_train_config(per_rank_batch_size, checkpoint_interval=1)
    train(
        model,
        build_dataset(),
        val_dataset=None,
        config=train_config,
        model_config=MODEL_CONFIG,
        checkpoint_dir=args.output_dir / "checkpoints",
    )

    weights = {name: p.detach().cpu().clone() for name, p in model.named_parameters()}
    torch.save(weights, args.output_dir / f"rank{rank}_weights.pt")


if __name__ == "__main__":
    main()
