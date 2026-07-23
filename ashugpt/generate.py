"""CLI: generate text from a trained AshuGPT checkpoint.

Usage:
    python -m ashugpt.generate --checkpoint checkpoints/demo/step_150.pt \
        --tokenizer tokenizer.json --prompt "Once upon a time" \
        --max-new-tokens 50 --temperature 0.8 --top-k 50 --top-p 0.9

    # Greedy decoding (temperature 0.0 disables sampling entirely):
    python -m ashugpt.generate --checkpoint ... --tokenizer ... \
        --prompt "Once upon a time" --temperature 0.0
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch

from ashugpt.inference.generate import generate_text
from ashugpt.tokenizer import BPETokenizer
from ashugpt.training.checkpoint import load_model_for_inference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, required=True, help="Path to a training checkpoint (.pt)")
    parser.add_argument("--tokenizer", type=Path, required=True, help="Path to a trained tokenizer JSON file")
    parser.add_argument("--prompt", type=str, required=True)
    parser.add_argument("--max-new-tokens", type=int, default=50)
    parser.add_argument("--temperature", type=float, default=1.0, help="0.0 = greedy decoding")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None, help="Set for reproducible sampling")
    parser.add_argument(
        "--no-cache", action="store_true", help="Disable the KV cache (recompute the whole sequence every step)"
    )
    args = parser.parse_args()

    if args.seed is not None:
        torch.manual_seed(args.seed)

    tokenizer = BPETokenizer.load(args.tokenizer)
    model = load_model_for_inference(args.checkpoint)
    model.eval()

    text = generate_text(
        model,
        tokenizer,
        args.prompt,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_k=args.top_k,
        top_p=args.top_p,
        use_cache=not args.no_cache,
    )
    print(text)


if __name__ == "__main__":
    main()
