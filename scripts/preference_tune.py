"""Preference-tune an instruction-tuned checkpoint with DPO.

This is the third kind of training run in the repo, and the differences
from the second one (scripts/finetune.py) are all consequences of one
thing: the objective compares two answers instead of imitating one.

  1. **Two models are loaded, from the same file.** The policy trains; the
     reference is frozen and only ever measured against. Both start as the
     SFT checkpoint, because DPO's derivation defines the implicit reward
     relative to the model the run began with -- see ashugpt/training/dpo.py.
     `--reference-from` exists for the unusual case of anchoring to a
     different model, and defaults to `--init-from`, which is what you want.
  2. **The data is pairs**, {instruction, input, chosen, rejected}, and an
     item is (2, seq_len). See ashugpt/data/preference.py.
  3. **Held-out numbers are ranking numbers**: accuracy and reward margin,
     not perplexity. The run prints them before the first step as well as
     after the last, because a DPO run's "before" is not a formality --
     accuracy starts at exactly 50% by construction, and an initial number
     that is not 50% means the policy and reference did not start equal.

What does *not* change is the training loop: the LR schedule, gradient
accumulation, mixed precision, logging, checkpointing and resume are all
`ashugpt.training.trainer.train()`, unchanged, because `DPOModel` presents
itself as a model whose forward pass returns a loss.

Usage:
    python scripts/preference_tune.py --model configs/model/medium.yaml \
        --train configs/train/dpo_hh.yaml \
        --init-from checkpoints/sft_dolly/step_470.pt \
        --data data/preference/hh.jsonl \
        --checkpoint-dir checkpoints/dpo_hh --log-path logs/dpo_hh.csv
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ashugpt.config import load_model_config, load_train_config
from ashugpt.data.preference import PreferenceDataset, PreferenceExample
from ashugpt.eval.preference import evaluate_preferences
from ashugpt.model import AshuGPT
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training import resolve_amp_dtype, train
from ashugpt.training.dpo import DPOModel

_IS_MAIN_PROCESS = int(os.environ.get("RANK", "0")) == 0


def read_jsonl(path: Path) -> list[PreferenceExample]:
    examples = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            examples.append(
                PreferenceExample(
                    instruction=row["instruction"],
                    input=row.get("input", ""),
                    chosen=row["chosen"],
                    rejected=row["rejected"],
                )
            )
    return examples


def load_weights(path: Path, model_config) -> AshuGPT:
    model = AshuGPT(model_config)
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["model_state_dict"])
    return model


def report(label: str, metrics: dict[str, float]) -> None:
    print(
        f"{label}: loss {metrics['loss']:.4f} | accuracy {metrics['accuracy']:.1%} | "
        f"margin {metrics['margin']:+.4f} "
        f"(chosen {metrics['chosen_reward']:+.4f}, rejected {metrics['rejected_reward']:+.4f}) | "
        f"policy alone: raw {metrics['raw_accuracy']:.1%}, per-token {metrics['length_normalized_accuracy']:.1%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--init-from", type=Path, required=True, help="Instruction-tuned checkpoint to start from")
    parser.add_argument(
        "--reference-from",
        type=Path,
        default=None,
        help="Checkpoint to freeze as the reference (default: --init-from, which is the standard choice)",
    )
    parser.add_argument("--data", type=Path, required=True, help="JSONL from scripts/prepare_preference_data.py")
    parser.add_argument("--val-fraction", type=float, default=0.02)
    parser.add_argument("--eval-batches", type=int, default=40, help="Held-out batches for the before/after report")
    parser.add_argument("--checkpoint-dir", type=Path, default=None)
    parser.add_argument("--log-path", type=Path, default=None)
    args = parser.parse_args()

    train_config = load_train_config(args.train)
    if train_config.parallel == "fsdp":
        # Not a limitation of DPO so much as of how the two features meet.
        # DPOModel.state_dict() returns the policy's, so a checkpoint holds a
        # plain model -- but FSDP does not go through it: the trainer gathers
        # its state with torch.distributed's own get_model_state_dict, which
        # walks the real module tree and would write "policy.*" and
        # "reference.*" keys that no inference path can load. Silently
        # producing an unloadable checkpoint at the end of an hour-long run
        # is a worse outcome than refusing here.
        raise SystemExit(
            "DPO does not support parallel: fsdp -- the checkpoint would carry both models under "
            "prefixed keys. Use ddp (the default); a preference run holds two copies of the model, "
            "not a model too large to replicate."
        )
    model_config = load_model_config(args.model)
    tokenizer = TiktokenBPETokenizer()

    examples = read_jsonl(args.data)
    random.Random(train_config.seed).shuffle(examples)
    n_val = max(1, int(len(examples) * args.val_fraction))
    val_examples, train_examples = examples[:n_val], examples[n_val:]

    train_dataset = PreferenceDataset(train_examples, tokenizer, seq_len=train_config.seq_len)
    val_dataset = PreferenceDataset(val_examples, tokenizer, seq_len=train_config.seq_len)

    if _IS_MAIN_PROCESS:
        dropped = train_dataset.dropped + val_dataset.dropped
        print(
            f"Data: {len(train_dataset):,} train / {len(val_dataset):,} val pairs "
            f"({dropped:,} dropped: identical answers, or one side longer than "
            f"{train_config.seq_len} tokens)"
        )
        chosen_len, rejected_len = train_dataset.mean_response_lengths
        print(
            f"Mean response length: chosen {chosen_len:.0f} tokens, rejected {rejected_len:.0f} "
            f"({chosen_len / rejected_len:.2f}x) -- see PreferenceDataset.mean_response_lengths "
            f"for why this is worth knowing before the run, not after"
        )

    torch.manual_seed(train_config.seed)
    reference_path = args.reference_from or args.init_from
    model = DPOModel(
        policy=load_weights(args.init_from, model_config),
        reference=load_weights(reference_path, model_config),
        beta=train_config.dpo_beta,
        length_normalized=train_config.dpo_length_normalized,
    )

    if _IS_MAIN_PROCESS:
        print(
            f"Model: {model_config.name} ({model.num_parameters():,} parameters), policy from "
            f"{args.init_from}, reference frozen from {reference_path}, beta {train_config.dpo_beta}"
            + (", length-normalized" if train_config.dpo_length_normalized else "")
        )

    # Before the first step, on the main process only: no collective has run
    # yet, so a single-rank forward pass here is safe. Accuracy must read
    # exactly 50.0% -- every pair is a tie because the policy and the
    # reference are the same weights. Anything else means they are not.
    amp_dtype = resolve_amp_dtype(train_config.amp_dtype)
    if _IS_MAIN_PROCESS:
        device = torch.device(f"cuda:{os.environ.get('LOCAL_RANK', '0')}" if torch.cuda.is_available() else "cpu")
        model.to(device)
        val_loader = DataLoader(val_dataset, batch_size=train_config.batch_size, shuffle=False)
        report("Before", evaluate_preferences(model, val_loader, amp_dtype, max_batches=args.eval_batches))
        model.train()

    train(
        model,
        train_dataset,
        val_dataset,
        train_config,
        model_config=model_config,
        checkpoint_dir=args.checkpoint_dir,
        log_path=args.log_path,
    )

    if _IS_MAIN_PROCESS:
        val_loader = DataLoader(val_dataset, batch_size=train_config.batch_size, shuffle=False)
        report("After ", evaluate_preferences(model, val_loader, amp_dtype, max_batches=args.eval_batches))


if __name__ == "__main__":
    main()
