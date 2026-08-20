"""Download and normalize a multi-turn chat corpus into JSONL.

Same idea as scripts/prepare_instruction_data.py -- write an intermediate
file rather than reading the HF dataset at train time, so a run is
reproducible and offline and you can read what the model is being trained
on -- but the unit here is a conversation, not a pair:

    {"messages": [{"role": "user", "content": ...},
                  {"role": "assistant", "content": ...}, ...]}

UltraChat 200k is the source. Its conversations are long (commonly 1,500+
tokens against this project's 512-token window), which is the whole reason
`ChatDataset` truncates at a turn boundary instead of dropping: dropping
would throw away most of the corpus.

Conversations are filtered, not repaired:

- **Must alternate user/assistant and start with a user turn.** A
  conversation that opens with the assistant, or has two user turns in a
  row, teaches a turn structure that generation will never encounter.
- **Must contain no role marker in its content.** `Turn` refuses those
  (see ashugpt/data/chat.py for why), so they are dropped here rather than
  raising in the middle of a training run.

Usage:
    python scripts/prepare_chat_data.py --output data/sft/ultrachat.jsonl --limit 20000
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ashugpt.data.chat import ASSISTANT_MARKER, SYSTEM_MARKER, USER_MARKER

REPO = "HuggingFaceH4/ultrachat_200k"
SPLIT = "train_sft"
MARKERS = tuple(marker.strip() for marker in (SYSTEM_MARKER, USER_MARKER, ASSISTANT_MARKER))


def is_well_formed(messages: list[dict]) -> bool:
    if len(messages) < 2:
        return False
    expected = "user"
    for message in messages:
        if message.get("role") != expected:
            return False
        content = (message.get("content") or "").strip()
        if not content or any(marker in content for marker in MARKERS):
            return False
        expected = "assistant" if expected == "user" else "user"
    # Ending on a user turn means the last thing in the document is a
    # question with no answer -- nothing to supervise at the end.
    return messages[-1]["role"] == "assistant"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20000, help="Conversations to keep")
    parser.add_argument("--max-turns", type=int, default=8, help="Trim longer conversations to this many turns")
    args = parser.parse_args()

    from datasets import load_dataset

    print(f"Streaming {REPO} ({SPLIT}) ...")
    dataset = load_dataset(REPO, split=SPLIT, streaming=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    kept = skipped = 0
    with args.output.open("w", encoding="utf-8") as fh:
        for row in dataset:
            if kept >= args.limit:
                break
            messages = [
                {"role": m["role"], "content": (m["content"] or "").strip()}
                for m in row.get("messages", [])
            ]
            # Trim to an even number of turns so the conversation still
            # ends on an assistant answer after the cut.
            messages = messages[: args.max_turns - (args.max_turns % 2)]
            if not is_well_formed(messages):
                skipped += 1
                continue
            fh.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
            kept += 1
            if kept % 5000 == 0:
                print(f"  {kept:,} conversations ...")

    print(f"Wrote {kept:,} conversations to {args.output} ({skipped:,} skipped as malformed)")


if __name__ == "__main__":
    main()
