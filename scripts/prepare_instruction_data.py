"""Download and normalize instruction-tuning datasets into one JSONL format.

Alpaca and Dolly describe the same thing with different field names, so both
are flattened to {instruction, input, output}. Writing an intermediate JSONL
(rather than reading the HF datasets directly at train time) keeps the training
run reproducible and offline, and makes it trivial to inspect what the model is
actually being trained on.

Usage:
    python scripts/prepare_instruction_data.py --dataset alpaca --output data/sft/alpaca.jsonl
    python scripts/prepare_instruction_data.py --dataset dolly  --output data/sft/dolly.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SOURCES = {
    "alpaca": {
        "repo": "tatsu-lab/alpaca",
        "split": "train",
        "fields": ("instruction", "input", "output"),
    },
    "dolly": {
        "repo": "databricks/databricks-dolly-15k",
        "split": "train",
        # Dolly calls the optional context "context" and the answer "response".
        "fields": ("instruction", "context", "response"),
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", choices=sorted(SOURCES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None, help="Keep only the first N examples")
    args = parser.parse_args()

    from datasets import load_dataset

    spec = SOURCES[args.dataset]
    instruction_key, input_key, output_key = spec["fields"]

    print(f"Downloading {spec['repo']} ...")
    ds = load_dataset(spec["repo"], split=spec["split"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    kept = skipped = 0
    with args.output.open("w", encoding="utf-8") as fh:
        for i, row in enumerate(ds):
            if args.limit is not None and kept >= args.limit:
                break
            instruction = (row.get(instruction_key) or "").strip()
            output = (row.get(output_key) or "").strip()
            # An example with no instruction or no answer teaches nothing, and
            # an empty response would be a masked-out example with zero
            # supervised positions.
            if not instruction or not output:
                skipped += 1
                continue
            fh.write(
                json.dumps(
                    {
                        "instruction": instruction,
                        "input": (row.get(input_key) or "").strip(),
                        "output": output,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            kept += 1

    print(f"Wrote {kept:,} examples to {args.output} ({skipped:,} skipped as empty)")


if __name__ == "__main__":
    main()
