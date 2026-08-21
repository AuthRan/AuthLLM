"""Download and normalize preference datasets into one JSONL format.

Same idea as scripts/prepare_instruction_data.py -- write an intermediate
file so a run is reproducible and offline and you can read what the model
is being trained on -- but the unit is a *pair*:

    {"instruction": ..., "input": "", "chosen": ..., "rejected": ...}

Two sources, which disagree about almost everything except that a human
ranked two answers:

**Anthropic/hh-rlhf** stores each side as one rendered transcript --
`"\\n\\nHuman: ...\\n\\nAssistant: ..."` -- with the prompt repeated inside
both. So the prompt has to be parsed back out of the transcript, and only
single-exchange conversations are kept: this repo's prompt template
(ashugpt/data/instruction.py) has one instruction slot and no way to
represent the earlier turns of a dialogue. Multi-turn preference data would
need the chat template instead, which is a different piece of work.

**HuggingFaceH4/ultrafeedback_binarized** already separates the prompt from
the two answers and gives each side as a message list, so the last message
is the answer. Its labels come from GPT-4 scoring rather than from humans;
its answers are also much longer and much more uniform in style, which
shows up directly in the length statistics `PreferenceDataset` prints.

Pairs are filtered, not repaired:

- **Both answers must be non-empty and different.** An identical pair
  carries no preference and no gradient (see PreferenceDataset).
- **hh-rlhf pairs must share a prompt.** The two transcripts of one row
  are the same conversation with different final answers; if they are not,
  the row is not a preference over one question and is dropped rather than
  guessed at.

Usage:
    python scripts/prepare_preference_data.py --dataset hh --output data/preference/hh.jsonl
    python scripts/prepare_preference_data.py --dataset hh --split test \\
        --output data/preference/hh_test.jsonl
    python scripts/prepare_preference_data.py --dataset ultrafeedback \\
        --output data/preference/ultrafeedback.jsonl --limit 20000
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Each source names its training split and the held-out split that ships with
# it. The test split matters more here than in supervised fine-tuning: a
# preference model is scored on whether it ranks *unseen pairs* correctly, and
# a slice cut out of the training file shares its prompts and its labellers
# with the data the run just fitted.
SOURCES = {
    "hh": {"repo": "Anthropic/hh-rlhf", "splits": {"train": "train", "test": "test"}},
    "ultrafeedback": {
        "repo": "HuggingFaceH4/ultrafeedback_binarized",
        "splits": {"train": "train_prefs", "test": "test_prefs"},
    },
}

# hh-rlhf renders every turn as "\n\nHuman: " / "\n\nAssistant: ".
_HH_TURN = re.compile(r"\n\n(Human|Assistant): ")


def split_hh_transcript(transcript: str) -> tuple[str, str] | None:
    """(prompt, answer) for a single-exchange hh-rlhf transcript, else None.

    Returns None for anything that is not exactly one Human turn followed
    by one Assistant turn: multi-turn dialogues (which this repo's
    single-turn template cannot represent), and malformed transcripts.
    """
    parts = _HH_TURN.split(transcript)
    # split() on a capturing pattern yields [before, role, text, role, text, ...].
    if parts[0].strip():
        return None  # text before the first turn marker: not a transcript
    turns = list(zip(parts[1::2], parts[2::2]))
    if len(turns) != 2:
        return None
    (first_role, prompt), (second_role, answer) = turns
    if first_role != "Human" or second_role != "Assistant":
        return None
    return prompt.strip(), answer.strip()


def rows_from_hh(row: dict) -> dict | None:
    chosen = split_hh_transcript(row.get("chosen") or "")
    rejected = split_hh_transcript(row.get("rejected") or "")
    if chosen is None or rejected is None:
        return None
    chosen_prompt, chosen_answer = chosen
    rejected_prompt, rejected_answer = rejected
    if chosen_prompt != rejected_prompt:
        return None
    return {"instruction": chosen_prompt, "chosen": chosen_answer, "rejected": rejected_answer}


def rows_from_ultrafeedback(row: dict) -> dict | None:
    chosen_messages = row.get("chosen") or []
    rejected_messages = row.get("rejected") or []
    if not chosen_messages or not rejected_messages:
        return None
    return {
        "instruction": (row.get("prompt") or "").strip(),
        "chosen": (chosen_messages[-1].get("content") or "").strip(),
        "rejected": (rejected_messages[-1].get("content") or "").strip(),
    }


EXTRACTORS = {"hh": rows_from_hh, "ultrafeedback": rows_from_ultrafeedback}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", choices=sorted(SOURCES), required=True)
    parser.add_argument("--split", choices=("train", "test"), default="train")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None, help="Keep only the first N pairs")
    args = parser.parse_args()

    from datasets import load_dataset

    spec = SOURCES[args.dataset]
    extract = EXTRACTORS[args.dataset]
    split = spec["splits"][args.split]

    print(f"Downloading {spec['repo']} ({split}) ...")
    dataset = load_dataset(spec["repo"], split=split)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    kept = skipped = 0
    with args.output.open("w", encoding="utf-8") as fh:
        for row in dataset:
            if args.limit is not None and kept >= args.limit:
                break
            pair = extract(row)
            if pair is None or not pair["instruction"] or not pair["chosen"] or not pair["rejected"]:
                skipped += 1
                continue
            if pair["chosen"] == pair["rejected"]:
                skipped += 1
                continue
            pair["input"] = ""  # this repo's template has an optional context slot; neither source fills it
            fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
            kept += 1
            if kept % 10000 == 0:
                print(f"  {kept:,} pairs ...")

    print(f"Wrote {kept:,} pairs to {args.output} ({skipped:,} skipped as multi-turn, empty, or identical)")


if __name__ == "__main__":
    main()
