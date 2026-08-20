"""Instruction-tune a pretrained AshuGPT checkpoint on prompt/response pairs.

Differs from scripts/train.py in three ways:

  1. Weights start from a pretrained checkpoint rather than random
     initialization. Only the weights are taken -- optimizer state, step count,
     and the LR schedule all start fresh, because this is a new run with its
     own (much lower) learning rate, not a resumption of the old one.
  2. The data is instruction examples, not a token stream, and the prompt is
     masked out of the loss. See ashugpt/data/instruction.py.
  3. Examples are whole and padded, so there is no windowing or stride.

Usage:
    python scripts/finetune.py --model configs/model/medium.yaml \
        --train configs/train/sft_alpaca.yaml \
        --init-from checkpoints/medium/step_20000.pt \
        --data data/sft/alpaca.jsonl \
        --checkpoint-dir checkpoints/sft_alpaca --log-path logs/sft_alpaca.csv
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import torch

from ashugpt.config import load_model_config, load_train_config
from ashugpt.data.instruction import InstructionDataset, InstructionExample, PackedInstructionDataset
from ashugpt.model import AshuGPT
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training import train

_IS_MAIN_PROCESS = int(os.environ.get("RANK", "0")) == 0


def read_jsonl(path: Path) -> list[InstructionExample]:
    examples = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            examples.append(
                InstructionExample(
                    instruction=row["instruction"],
                    input=row.get("input", ""),
                    output=row["output"],
                )
            )
    return examples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--init-from", type=Path, required=True, help="Pretrained checkpoint to start from")
    parser.add_argument("--data", type=Path, required=True, help="JSONL from scripts/prepare_instruction_data.py")
    parser.add_argument("--val-fraction", type=float, default=0.02)
    parser.add_argument("--checkpoint-dir", type=Path, default=None)
    parser.add_argument("--log-path", type=Path, default=None)
    args = parser.parse_args()

    train_config = load_train_config(args.train)
    model_config = load_model_config(args.model)
    tokenizer = TiktokenBPETokenizer()

    examples = read_jsonl(args.data)
    random.Random(train_config.seed).shuffle(examples)
    n_val = max(1, int(len(examples) * args.val_fraction))
    val_examples, train_examples = examples[:n_val], examples[n_val:]

    # Validation stays unpacked whichever mode training uses. Held-out loss is
    # averaged per batch, so packing the val set would silently reweight it --
    # a packed batch holds ~9x the supervised tokens of an unpacked one, and
    # the two numbers would no longer be comparable to every run already
    # logged. Packing is a training-throughput change, not an eval change.
    dataset_cls = PackedInstructionDataset if train_config.pack_sequences else InstructionDataset
    train_dataset = dataset_cls(train_examples, tokenizer, seq_len=train_config.seq_len)
    val_dataset = InstructionDataset(val_examples, tokenizer, seq_len=train_config.seq_len)

    if _IS_MAIN_PROCESS:
        dropped = train_dataset.dropped + val_dataset.dropped
        n_train = len(train_examples) - train_dataset.dropped
        print(
            f"Data: {n_train:,} train / {len(val_dataset):,} val examples "
            f"({dropped:,} dropped as longer than {train_config.seq_len} tokens)"
        )
        if train_config.pack_sequences:
            print(
                f"Packing: {n_train:,} examples into {len(train_dataset):,} windows "
                f"({n_train / len(train_dataset):.1f} per window, "
                f"{train_dataset.packing_efficiency:.1%} of positions used)"
            )

    torch.manual_seed(train_config.seed)
    model = AshuGPT(model_config)

    # Weights only. A fine-tune is a new run: loading the pretraining
    # optimizer's momentum and variance would carry 6e-4-scale statistics into
    # a 2e-5 run, and its step count would start this run at the end of a
    # cosine schedule it never took part in.
    checkpoint = torch.load(args.init_from, map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["model_state_dict"])
    if _IS_MAIN_PROCESS:
        print(
            f"Model: {model_config.name} ({model.num_parameters():,} parameters), "
            f"initialized from {args.init_from} (step {checkpoint.get('step')})"
        )

    train(
        model,
        train_dataset,
        val_dataset,
        train_config,
        model_config=model_config,
        checkpoint_dir=args.checkpoint_dir,
        log_path=args.log_path,
    )


if __name__ == "__main__":
    main()
