"""Train an AshuGPT model from scratch on a text corpus.

Usage (verify the pipeline quickly on the small synthetic corpus):
    python scripts/train_tokenizer.py --input tests/fixtures/synthetic_corpus.txt \
        --vocab-size 300 --output .tokenizer_demo.json
    python scripts/train.py --model configs/model/tiny.yaml --train configs/train/tiny_cpu.yaml \
        --tokenizer .tokenizer_demo.json --input tests/fixtures/synthetic_corpus.txt \
        --checkpoint-dir checkpoints/demo

Multi-GPU (or, for testing, multi-process-on-CPU) training via torchrun --
no flags change, the same command becomes distributed just by how it's launched:
    torchrun --nproc_per_node=2 scripts/train.py --model ... --train ... \
        --tokenizer ... --input ... --checkpoint-dir checkpoints/demo
"""

from __future__ import annotations

import argparse
import dataclasses
import os
from pathlib import Path

import torch

from ashugpt.config import load_model_config, load_train_config
from ashugpt.data import ShardedTokenDataset, TokenizedDataset, load_and_tokenize, split_train_val
from ashugpt.model import AshuGPT
from ashugpt.tokenizer import load_tokenizer
from ashugpt.training import train

_IS_MAIN_PROCESS = int(os.environ.get("RANK", "0")) == 0  # cheap pre-check, before train() sets up torch.distributed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="Path to a configs/model/*.yaml preset")
    parser.add_argument("--train", type=Path, required=True, help="Path to a configs/train/*.yaml preset")
    parser.add_argument("--tokenizer", type=Path, required=True, help="Path to a trained tokenizer JSON file")
    parser.add_argument("--input", type=Path, help="Text corpus to train on (in-memory path; small corpora)")
    parser.add_argument(
        "--data-manifest",
        type=Path,
        default=None,
        help="manifest.json from scripts/prepare_data.py (memory-mapped shard path; large corpora). "
        "Mutually exclusive with --input, and takes precedence over it.",
    )
    parser.add_argument(
        "--token-cache",
        type=Path,
        default=None,
        help="Optional .npy path to cache the tokenized corpus (skips re-tokenizing on later runs)",
    )
    parser.add_argument("--val-fraction", type=float, default=0.1)
    parser.add_argument("--checkpoint-dir", type=Path, default=None)
    parser.add_argument("--resume-from", type=Path, default=None)
    parser.add_argument("--log-path", type=Path, default=None)
    args = parser.parse_args()
    if args.data_manifest is None and args.input is None:
        parser.error("one of --input (small corpus) or --data-manifest (sharded corpus) is required")

    tokenizer = load_tokenizer(args.tokenizer)
    model_config = load_model_config(args.model)
    if model_config.vocab_size < tokenizer.vocab_size:
        # Too small is a real error -- the tokenizer can emit ids the embedding
        # table has no row for.
        model_config = dataclasses.replace(model_config, vocab_size=tokenizer.vocab_size)
    elif model_config.vocab_size > tokenizer.vocab_size and _IS_MAIN_PROCESS:
        # Too large is fine, and deliberate. The presets use 50304 = 50259
        # rounded up to a multiple of 64, because GPU tensor cores want
        # matrix dimensions that are multiples of 8/64; the extra rows are
        # unreachable ids that simply never receive gradient. Shrinking the
        # config to fit the tokenizer exactly would cost throughput for
        # nothing, so the padding is kept.
        print(
            f"Model vocab_size {model_config.vocab_size} > tokenizer vocab_size {tokenizer.vocab_size} "
            f"({model_config.vocab_size - tokenizer.vocab_size} unused padding rows, kept for tensor-core alignment)"
        )
    train_config = load_train_config(args.train)

    if args.data_manifest is not None:
        train_dataset = ShardedTokenDataset.from_manifest(
            args.data_manifest, seq_len=train_config.seq_len, split="train", stride=train_config.stride
        )
        val_dataset = ShardedTokenDataset.from_manifest(
            args.data_manifest, seq_len=train_config.seq_len, split="val", stride=train_config.stride
        )
        if _IS_MAIN_PROCESS:
            print(
                f"Data: {train_dataset.total_tokens:,} train tokens / {val_dataset.total_tokens:,} val tokens "
                f"({len(train_dataset):,} windows at stride {train_dataset.stride})"
            )
    else:
        token_ids = load_and_tokenize(args.input, tokenizer, cache_path=args.token_cache)
        train_ids, val_ids = split_train_val(token_ids, val_fraction=args.val_fraction)
        stride = train_config.stride if train_config.stride is not None else 1
        train_dataset = TokenizedDataset(train_ids, seq_len=train_config.seq_len, stride=stride)
        val_dataset = TokenizedDataset(val_ids, seq_len=train_config.seq_len, stride=stride)

    # Every rank constructs its own model instance identically (same seed);
    # DDP wrapping inside train() broadcasts rank 0's weights to every
    # other rank as well, so this doesn't need to be distributed-aware.
    torch.manual_seed(train_config.seed)
    model = AshuGPT(model_config)
    if _IS_MAIN_PROCESS:
        print(f"Model: {model_config.name} ({model.num_parameters():,} parameters)")

    train(
        model,
        train_dataset,
        val_dataset,
        train_config,
        model_config=model_config,
        checkpoint_dir=args.checkpoint_dir,
        resume_from=args.resume_from,
        log_path=args.log_path,
    )


if __name__ == "__main__":
    main()
