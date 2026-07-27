"""Strip a training checkpoint down to an inference-only checkpoint.

A training checkpoint bundles optimizer state (AdamW's per-parameter momentum
and variance -- roughly 2x the model's size in fp32) so training can resume.
None of that is needed to *run* the model, so shipping it to a demo/Space just
wastes space. This tool keeps only what inference needs -- the weights and the
model config -- producing a much smaller file that
`load_model_for_inference` loads unchanged.

Usage:
    python scripts/export_inference.py --checkpoint required_downloads/step_4000.pt \
        --output space/model.pt
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, required=True, help="A training checkpoint (.pt)")
    parser.add_argument("--output", type=Path, required=True, help="Where to write the inference-only checkpoint")
    args = parser.parse_args()

    ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    # Keep exactly the keys load_model_for_inference reads, plus step for provenance.
    slim = {
        "model_config": ckpt["model_config"],
        "model_state_dict": ckpt["model_state_dict"],
        "step": ckpt.get("step"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(slim, args.output)

    before = args.checkpoint.stat().st_size / 1e6
    after = args.output.stat().st_size / 1e6
    print(f"Exported {args.output} — {before:.0f}MB -> {after:.0f}MB (step {slim['step']})")


if __name__ == "__main__":
    main()
