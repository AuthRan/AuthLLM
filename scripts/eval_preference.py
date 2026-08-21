"""Score checkpoints on held-out preference pairs, side by side.

`scripts/eval_instruction_following.py` answers "did the model's behaviour
change" for a supervised fine-tune. This answers the question a preference
run raises instead: **does the model rank a better answer above a worse one
on pairs nobody trained on**, and is that ranking real or is it length?

Three numbers per checkpoint, all on the same pairs:

  raw accuracy        Does the policy assign more probability to the chosen
                      answer than the rejected one? Reference-free, so the
                      SFT model the run started from can be scored with the
                      identical metric -- which is what makes the comparison
                      a comparison rather than a report.
  per-token accuracy  The same, with each answer's log-probability divided
                      by its length. Summed log-probabilities punish long
                      answers for being long, so a model that learned only
                      "prefer the shorter one" scores well on the first
                      number and not on this one. The gap between them is
                      the size of the length effect, and the two columns
                      beside it -- raw accuracy on the pairs where the
                      chosen answer is shorter, and on the pairs where it
                      is longer -- say outright how much of the ranking is
                      length. A pure length detector reads 100% and 0%.
  DPO accuracy        The implicit-reward ranking against `--reference`,
                      the quantity the run actually optimized. Exactly 50%
                      for the reference itself, by construction.

The pairs should come from the source's own test split
(`scripts/prepare_preference_data.py --split test`), not from a slice of the
training file: a held-out slice of the same file shares its prompts and its
labellers with the data the run just fitted.

Usage:
    python scripts/eval_preference.py --data data/preference/hh_test.jsonl \
        --reference checkpoints/sft_dolly_packed3e5/step_940.pt \
        --checkpoint sft=checkpoints/sft_dolly_packed3e5/step_940.pt \
        --checkpoint dpo=checkpoints/dpo_hh/step_1525.pt \
        --output results/preference_eval_hh.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ashugpt.data.preference import PreferenceDataset, PreferenceExample
from ashugpt.eval.preference import evaluate_preferences
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training.amp import resolve_amp_dtype
from ashugpt.training.checkpoint import load_model_for_inference
from ashugpt.training.dpo import DPOModel

COLUMNS = ("raw_accuracy", "length_normalized_accuracy", "accuracy", "margin", "loss")


def read_jsonl(path: Path) -> list[PreferenceExample]:
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    return [
        PreferenceExample(r["instruction"], r.get("input", ""), r["chosen"], r["rejected"]) for r in rows
    ]


def parse_checkpoint(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"--checkpoint wants name=path, got {value!r}")
    name, path = value.split("=", 1)
    return name, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="Preference JSONL to score on")
    parser.add_argument(
        "--reference",
        type=Path,
        required=True,
        help="The checkpoint the DPO run was anchored to -- the same one for every column, or the "
        "implicit rewards are not comparable",
    )
    parser.add_argument("--checkpoint", type=parse_checkpoint, action="append", required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--seq-len", type=int, default=512)
    parser.add_argument("--max-batches", type=int, default=None, help="Default: every pair in the file")
    parser.add_argument("--amp-dtype", default="none", choices=("none", "float16", "bfloat16"))
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--output", type=Path, default=None, help="Write the table to this markdown file")
    args = parser.parse_args()

    tokenizer = TiktokenBPETokenizer()
    dataset = PreferenceDataset(read_jsonl(args.data), tokenizer, seq_len=args.seq_len)
    chosen_len, rejected_len = dataset.mean_response_lengths
    print(
        f"{len(dataset):,} pairs from {args.data} ({dataset.dropped:,} dropped) | "
        f"mean response: chosen {chosen_len:.0f} tokens, rejected {rejected_len:.0f}"
    )

    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=False)
    amp_dtype = resolve_amp_dtype(args.amp_dtype)
    reference = load_model_for_inference(args.reference)

    rows = {}
    for name, path in args.checkpoint:
        model = DPOModel(load_model_for_inference(path), reference, beta=0.1).to(args.device)
        rows[name] = evaluate_preferences(model, loader, amp_dtype, max_batches=args.max_batches)
        m = rows[name]
        print(
            f"{name:>12}: raw {m['raw_accuracy']:.1%} "
            f"(chosen shorter {m['raw_accuracy_chosen_shorter']:.1%}, "
            f"longer {m['raw_accuracy_chosen_longer']:.1%}) | "
            f"per-token {m['length_normalized_accuracy']:.1%} | "
            f"DPO {m['accuracy']:.1%} | margin {m['margin']:+.4f} | loss {m['loss']:.4f}"
        )
        del model
        if args.device.startswith("cuda"):
            torch.cuda.empty_cache()

    if args.output is not None:
        lines = [
            f"# Preference ranking on `{args.data}`",
            "",
            f"{len(dataset):,} held-out pairs. Mean response length: chosen {chosen_len:.0f} tokens, "
            f"rejected {rejected_len:.0f}. Implicit rewards are measured against "
            f"`{args.reference}`, so its own row is 50% by construction.",
            "",
            "| checkpoint | raw accuracy | chosen shorter | chosen longer | per-token accuracy | "
            "DPO accuracy | margin | loss |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for name, metrics in rows.items():
            lines.append(
                f"| `{name}` | {metrics['raw_accuracy']:.1%} | "
                f"{metrics['raw_accuracy_chosen_shorter']:.1%} | "
                f"{metrics['raw_accuracy_chosen_longer']:.1%} | "
                f"{metrics['length_normalized_accuracy']:.1%} | {metrics['accuracy']:.1%} | "
                f"{metrics['margin']:+.4f} | {metrics['loss']:.4f} |"
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
