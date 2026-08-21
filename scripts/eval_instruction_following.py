"""Measure what instruction tuning changed, across checkpoints, on one held-out set.

`scripts/evaluate.py` reports loss on a token stream and `scripts/sample.py`
prints text you have to read yourself. Neither answers the question a
fine-tune actually raises: *did the model's behaviour change, and by how
much*. This script answers it with two kinds of number, both computed on the
same held-out examples for every checkpoint given, so the columns are
comparable.

  1. **Held-out loss** on instruction data. The prompt is masked exactly as in
     training, so this scores only how well the model predicts the *response*
     -- a base model that writes beautiful continuations of the instruction
     still scores badly here, which is the point.

  2. **Generation behaviour**, from real sampled completions:
       - `stop rate` -- fraction that emit EOS before the token cap. This is
         the single clearest base-vs-tuned difference: nothing in pretraining
         ever taught the model that a document can end, so a base model runs
         to the cap essentially always.
       - `mean tokens` -- how long the answers are.
       - `loop rate` -- fraction containing a repeated 10-token window, the
         degeneration mode small models fall into when the next-token
         distribution is shallow.

The held-out split is reproduced rather than stored: same `--seed` and
`--val-fraction` as scripts/finetune.py, over the same JSONL, yields the same
examples the fine-tune never trained on.

Usage:
    python scripts/eval_instruction_following.py --data data/sft/dolly.jsonl \
        --checkpoint base=checkpoints/medium/step_20000.pt \
        --checkpoint alpaca=checkpoints/sft_alpaca/step_1600.pt \
        --output results/instruction_eval.md
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ashugpt.data.instruction import InstructionDataset, InstructionExample
from ashugpt.eval.generation import has_repeated_window
from ashugpt.eval.perplexity import evaluate
from ashugpt.inference.generate import generate
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training.checkpoint import load_model_for_inference


def read_jsonl(path: Path) -> list[InstructionExample]:
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    return [InstructionExample(r["instruction"], r.get("input", ""), r["output"]) for r in rows]


def held_out_split(examples: list[InstructionExample], seed: int, val_fraction: float) -> list[InstructionExample]:
    """The same slice scripts/finetune.py held out -- same shuffle, same cut."""
    examples = list(examples)
    random.Random(seed).shuffle(examples)
    n_val = max(1, int(len(examples) * val_fraction))
    return examples[:n_val]


def measure_generation(model, tokenizer, examples, args) -> tuple[dict[str, float], list[dict]]:
    device = next(model.parameters()).device
    records = []
    for example in examples:
        # Re-seeded per prompt so any single row here reproduces on its own
        # via scripts/sample.py, without replaying the whole batch.
        torch.manual_seed(args.seed)
        prompt_ids = tokenizer.encode(example.prompt(), add_bos=True)
        input_ids = torch.tensor([prompt_ids], device=device)
        output_ids = generate(
            model,
            input_ids,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_k=args.top_k,
            eos_id=tokenizer.eos_id,
        )
        new_ids = output_ids[0, len(prompt_ids) :].tolist()
        # generate() stops the whole batch at EOS and pads finished rows with
        # more EOS, so "did it stop" is "is there an EOS in what came back",
        # and the answer text is everything before the first one.
        stopped = tokenizer.eos_id in new_ids
        answer_ids = new_ids[: new_ids.index(tokenizer.eos_id)] if stopped else new_ids
        records.append(
            {
                "instruction": example.instruction,
                "input": example.input,
                "reference": example.output,
                "answer": tokenizer.decode(answer_ids).strip(),
                "stopped": stopped,
                "tokens": len(answer_ids),
                "looped": has_repeated_window(answer_ids),
            }
        )

    n = len(records)
    summary = {
        "stop_rate": sum(r["stopped"] for r in records) / n,
        "mean_tokens": sum(r["tokens"] for r in records) / n,
        "loop_rate": sum(r["looped"] for r in records) / n,
    }
    return summary, records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--checkpoint",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="Repeatable. The label names the column in the summary table.",
    )
    parser.add_argument("--data", type=Path, required=True, help="Instruction JSONL to hold examples out of")
    parser.add_argument("--val-fraction", type=float, default=0.02, help="Must match the fine-tune's value")
    parser.add_argument("--split-seed", type=int, default=1337, help="Must match the fine-tune's train config seed")
    parser.add_argument("--loss-batches", type=int, default=40, help="Batches of held-out loss to average")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--seq-len", type=int, default=512)
    parser.add_argument("--n-prompts", type=int, default=40, help="Held-out prompts to actually generate from")
    parser.add_argument("--max-new-tokens", type=int, default=200)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--output", type=Path, default=None, help="Write every generation to this markdown file")
    args = parser.parse_args()

    checkpoints = []
    for spec in args.checkpoint:
        if "=" not in spec:
            parser.error(f"--checkpoint expects LABEL=PATH, got {spec!r}")
        label, path = spec.split("=", 1)
        checkpoints.append((label, Path(path)))

    tokenizer = TiktokenBPETokenizer()
    val_examples = held_out_split(read_jsonl(args.data), args.split_seed, args.val_fraction)
    print(f"Held out {len(val_examples):,} examples from {args.data}")

    loss_dataset = InstructionDataset(val_examples, tokenizer, seq_len=args.seq_len)
    loss_loader = DataLoader(loss_dataset, batch_size=args.batch_size, shuffle=False)
    prompt_examples = val_examples[: args.n_prompts]

    results = {}
    generations = {}
    for label, path in checkpoints:
        print(f"\n=== {label}: {path} ===")
        model = load_model_for_inference(path).to(args.device).eval()
        # fp16 autocast here would report a loss the training run never saw;
        # held-out numbers are worth the extra second in fp32.
        loss = evaluate(model, loss_loader, amp_dtype=None, max_batches=args.loss_batches)
        summary, records = measure_generation(model, tokenizer, prompt_examples, args)
        results[label] = {**loss, **summary}
        generations[label] = records
        print(
            f"loss {loss['loss']:.4f} | ppl {loss['perplexity']:.2f} | "
            f"stop {summary['stop_rate']:.0%} | mean {summary['mean_tokens']:.0f} tok | "
            f"loop {summary['loop_rate']:.0%}"
        )
        del model
        if args.device.startswith("cuda"):
            torch.cuda.empty_cache()

    header = "| checkpoint | held-out loss | ppl | stop rate | mean tokens | loop rate |"
    rule = "| --- | ---: | ---: | ---: | ---: | ---: |"
    rows = [
        f"| `{label}` | {r['loss']:.4f} | {r['perplexity']:.2f} | {r['stop_rate']:.0%} | "
        f"{r['mean_tokens']:.0f} | {r['loop_rate']:.0%} |"
        for label, r in results.items()
    ]
    table = "\n".join([header, rule, *rows])
    print(f"\n{table}")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as fh:
            fh.write(f"# Instruction-following eval — {args.data.name}\n\n")
            fh.write(
                f"{len(val_examples):,} held-out examples (seed {args.split_seed}, "
                f"val fraction {args.val_fraction}); loss over {args.loss_batches} batches of "
                f"{args.batch_size}; generations from the first {len(prompt_examples)} held-out "
                f"prompts at temperature {args.temperature}, top_k {args.top_k}, "
                f"cap {args.max_new_tokens} tokens.\n\n"
            )
            fh.write(table + "\n")
            for label, records in generations.items():
                fh.write(f"\n## {label}\n")
                for record in records:
                    fh.write(f"\n### {record['instruction']}\n\n")
                    if record["input"]:
                        fh.write(f"*Input:* {record['input']}\n\n")
                    stop_note = "stopped" if record["stopped"] else f"hit the {args.max_new_tokens}-token cap"
                    fh.write(f"`{record['tokens']} tokens, {stop_note}`\n\n")
                    answer = record["answer"] or "(empty)"
                    fh.write("\n".join(f"> {line}" for line in answer.splitlines()) + "\n")
        print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
