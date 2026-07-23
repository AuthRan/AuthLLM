"""Evaluate a trained AshuGPT checkpoint: validation loss + perplexity on a text file.

Usage:
    python scripts/evaluate.py --checkpoint checkpoints/demo/step_150.pt \
        --tokenizer tokenizer.json --input tests/fixtures/synthetic_corpus.txt --seq-len 16
"""

from __future__ import annotations

import argparse
from pathlib import Path

from torch.utils.data import DataLoader

from ashugpt.data import TokenizedDataset, load_and_tokenize
from ashugpt.eval.perplexity import evaluate
from ashugpt.tokenizer import BPETokenizer
from ashugpt.training.amp import resolve_amp_dtype
from ashugpt.training.checkpoint import load_model_for_inference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True, help="Text file to evaluate on")
    parser.add_argument("--seq-len", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-batches", type=int, default=None, help="Limit how many batches to average over")
    parser.add_argument("--amp-dtype", type=str, default="none", choices=["bfloat16", "float16", "none"])
    args = parser.parse_args()

    tokenizer = BPETokenizer.load(args.tokenizer)
    model = load_model_for_inference(args.checkpoint)
    print(f"Loaded checkpoint: {model.num_parameters():,} parameters")

    token_ids = load_and_tokenize(args.input, tokenizer)
    loader = DataLoader(TokenizedDataset(token_ids, seq_len=args.seq_len), batch_size=args.batch_size, shuffle=False)

    metrics = evaluate(model, loader, resolve_amp_dtype(args.amp_dtype), max_batches=args.max_batches)
    print(f"loss:       {metrics['loss']:.4f}")
    print(f"perplexity: {metrics['perplexity']:.2f}")


if __name__ == "__main__":
    main()
