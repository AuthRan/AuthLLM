"""Demo: fetch GPT-2's REAL config and checkpoint metadata live from
Hugging Face, then run AshuGPT's pretrained loader against it.

Deliberately does NOT download the full ~548MB of GPT-2 weight data --
the question this milestone answers (is the architecture compatible?) is
fully decided by tensor NAMES and SHAPES, which this script fetches for
real (config.json in full; the safetensors header via an HTTP range
request, which lists every tensor's name/shape/dtype without downloading
its data). The actual floating-point values loaded here are placeholders
(torch.zeros, correctly shaped) -- clearly not claiming to be the real
trained weights, because they aren't.

Usage:
    python scripts/demo_pretrained_loading.py
    python scripts/demo_pretrained_loading.py --model gpt2-medium
"""

from __future__ import annotations

import argparse
import json
import urllib.request

import torch

from ashugpt.inference.pretrained_loader import IncompatibleArchitectureError, load_gpt2_checkpoint

HF_BASE = "https://huggingface.co/openai-community/{model}/resolve/main"


def fetch_config(model: str) -> dict:
    with urllib.request.urlopen(f"{HF_BASE.format(model=model)}/config.json", timeout=15) as resp:
        return json.load(resp)


def fetch_safetensors_header(model: str, max_bytes: int = 200_000) -> dict:
    """The safetensors format starts with an 8-byte little-endian header
    length, then that many bytes of JSON describing every tensor's name,
    dtype, shape, and byte offset -- fetching just that (via a Range
    request) tells us the complete real key/shape structure without
    downloading any actual tensor data."""
    req = urllib.request.Request(
        f"{HF_BASE.format(model=model)}/model.safetensors", headers={"Range": f"bytes=0-{max_bytes}"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    header_len = int.from_bytes(data[:8], "little")
    if 8 + header_len > len(data):
        raise RuntimeError(f"header ({header_len} bytes) exceeds fetched range ({max_bytes}); increase max_bytes")
    return json.loads(data[8 : 8 + header_len])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="gpt2", help="e.g. gpt2, gpt2-medium, gpt2-large")
    args = parser.parse_args()

    print(f"Fetching real config.json and safetensors header for '{args.model}' from Hugging Face...")
    hf_config = fetch_config(args.model)
    header = fetch_safetensors_header(args.model)
    tensor_specs = {k: v for k, v in header.items() if k != "__metadata__"}
    print(f"  {len(tensor_specs)} real tensor names/shapes fetched (no weight data downloaded)\n")

    gpt2_config = {
        "vocab_size": hf_config["vocab_size"],
        "n_positions": hf_config["n_positions"],
        "n_embd": hf_config["n_embd"],
        "n_layer": hf_config["n_layer"],
        "n_head": hf_config["n_head"],
    }
    print("Real GPT-2 config:", gpt2_config)

    # Placeholder tensors at the REAL shapes -- not the real trained values.
    source_state_dict = {name: torch.zeros(spec["shape"]) for name, spec in tensor_specs.items()}

    print("\n--- Attempting load_gpt2_checkpoint(..., strict=True) [the default] ---")
    try:
        load_gpt2_checkpoint(source_state_dict, gpt2_config=gpt2_config, strict=True)
        print("Unexpectedly succeeded -- this should not happen for a GPT-2 checkpoint.")
    except IncompatibleArchitectureError as e:
        print("Raised IncompatibleArchitectureError, as expected:\n")
        print(e)

    print("\n--- Loading again with strict=False (inspection only, NOT a usable model) ---")
    model, report = load_gpt2_checkpoint(source_state_dict, gpt2_config=gpt2_config, strict=False)
    print(f"Model constructed: {model.num_parameters():,} parameters (shape-matched to real {args.model})")
    print(f"Successfully mapped tensors: {sum(1 for _ in model.state_dict()) - len(report.missing_keys)}")
    print(report.summary())


if __name__ == "__main__":
    main()
