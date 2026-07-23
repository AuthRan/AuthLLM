"""Train a BPE tokenizer from a text file and save it to disk.

Usage:
    python scripts/train_tokenizer.py --input tests/fixtures/tiny_corpus.txt \
        --vocab-size 2000 --output tokenizer.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ashugpt.tokenizer import BPETokenizer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 text file to train on")
    parser.add_argument("--vocab-size", type=int, default=2000, help="Target vocabulary size")
    parser.add_argument("--output", type=Path, required=True, help="Where to save the trained tokenizer (JSON)")
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8")
    tokenizer = BPETokenizer.train(text, vocab_size=args.vocab_size)
    tokenizer.save(args.output)

    print(f"Trained on {len(text):,} characters from {args.input}")
    print(f"vocab_size={tokenizer.vocab_size}, merges_learned={len(tokenizer.merges)}")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
