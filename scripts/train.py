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
from ashugpt.data import TokenizedDataset, load_and_tokenize, split_train_val
from ashugpt.model import AshuGPT
from ashugpt.tokenizer import BPETokenizer
from ashugpt.training import train

_IS_MAIN_PROCESS = int(os.environ.get("RANK", "0")) == 0  # cheap pre-check, before train() sets up torch.distributed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="Path to a configs/model/*.yaml preset")
    parser.add_argument("--train", type=Path, required=True, help="Path to a configs/train/*.yaml preset")
    parser.add_argument("--tokenizer", type=Path, required=True, help="Path to a trained tokenizer JSON file")
    parser.add_argument("--input", type=Path, required=True, help="Text corpus to train on")
    parser.add_argument("--val-fraction", type=float, default=0.1)
    parser.add_argument("--checkpoint-dir", type=Path, default=None)
    parser.add_argument("--resume-from", type=Path, default=None)
    parser.add_argument("--log-path", type=Path, default=None)
    args = parser.parse_args()

    tokenizer = BPETokenizer.load(args.tokenizer)
    model_config = load_model_config(args.model)
    if model_config.vocab_size != tokenizer.vocab_size:
        model_config = dataclasses.replace(model_config, vocab_size=tokenizer.vocab_size)
    train_config = load_train_config(args.train)

    token_ids = load_and_tokenize(args.input, tokenizer)
    train_ids, val_ids = split_train_val(token_ids, val_fraction=args.val_fraction)
    train_dataset = TokenizedDataset(train_ids, seq_len=train_config.seq_len)
    val_dataset = TokenizedDataset(val_ids, seq_len=train_config.seq_len)

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
