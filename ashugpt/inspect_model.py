"""CLI: report a model configuration's size and estimated memory
requirements WITHOUT building or training the model.

Usage:
    python -m ashugpt.inspect_model --config tiny
    python -m ashugpt.inspect_model --config 1b --batch-size 4 --seq-len 2048
    python -m ashugpt.inspect_model --config configs/model/medium.yaml
    python -m ashugpt.inspect_model --all              # every built-in preset, side by side
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ashugpt.config import ModelConfig, load_model_config
from ashugpt.utils.memory import estimate_memory

PRESET_DIR = Path(__file__).resolve().parent.parent / "configs" / "model"
PRESET_NAMES = ["tiny", "small", "medium", "xl_1b"]
PRESET_ALIASES = {"1b": "xl_1b", "xl": "xl_1b"}  # names someone might reasonably type for the 1B+ preset

EXPLANATION = """\
------------------------------------------------------------------------
Implemented architecture vs. trained model vs. pretrained checkpoint
------------------------------------------------------------------------
Everything printed above describes an ARCHITECTURE CONFIGURATION: shape
hyperparameters that ashugpt/model/gpt.py can build into a real
nn.Module with exactly that many randomly-initialized parameters, plus
a memory estimate for what training or serving that many parameters
would cost. Computing this report took a fraction of a second and used
no GPU -- it is pure arithmetic on the config's shape (see
ModelConfig.approx_param_count() / ashugpt/utils/memory.py), not a
description of anything that was actually built or run.

Three genuinely different things, easy to conflate:

  1. Implemented architecture
     A configuration this codebase CAN construct (this report). Has a
     parameter count and a memory estimate. Every weight would be
     freshly random-initialized if actually built -- nothing has learned
     anything.

  2. Model trained from scratch
     An architecture that was ACTUALLY trained: gradient descent
     actually ran, on actual data, for actual optimizer steps, and a
     real checkpoint file exists (ashugpt/training/checkpoint.py,
     the checkpoints/ directory -- gitignored, self-trained only).
     Every checkpoint this project can currently load was trained this
     way, at `tiny`/`small` scale, on the small demo corpora in
     tests/fixtures/ -- see SPEC.md's milestone log. Nothing at
     `medium` or `1b` scale has been trained here; this project's
     CPU-only environment makes that impractical (see README.md's
     Memory Optimization section for measured, not assumed, evidence
     of exactly why).

  3. Pretrained checkpoint loaded for inference
     Public weights (e.g. GPT-2's) downloaded and mapped onto this
     architecture for INFERENCE ONLY -- never trained by this project,
     never fine-tuned here. This is SPEC.md milestone M13
     (pretrained_loader.py), not yet built. Its checkpoints would live
     in pretrained/ (also gitignored), kept strictly separate from
     checkpoints/ so a self-trained model is never confused with a
     downloaded one.

The preset(s) reported above are case 1 only. Report != training run.
------------------------------------------------------------------------
"""


def _resolve_config_path(name: str) -> Path:
    as_path = Path(name)
    if as_path.exists():
        return as_path
    filename = PRESET_ALIASES.get(name, name)
    path = PRESET_DIR / f"{filename}.yaml"
    if not path.exists():
        available = ", ".join(PRESET_NAMES + list(PRESET_ALIASES))
        raise SystemExit(f"Unknown config '{name}'. Available presets: {available} (or pass a path to a YAML file)")
    return path


def _report(config: ModelConfig, batch_size: int, seq_len: int, optimizer: str) -> None:
    estimate = estimate_memory(config, batch_size=batch_size, seq_len=seq_len, optimizer=optimizer)

    print(f"=== {config.name} ===  [ARCHITECTURE CONFIGURATION -- not a trained model; see explanation below]")
    print(f"Layers:                       {config.n_layers}")
    print(f"Hidden dimension:             {config.d_model}")
    print(f"Attention heads:              {config.n_heads}")
    print(f"Vocabulary size:              {config.vocab_size:,}")
    print(f"Context length:               {config.context_length}")
    for line in estimate.summary_lines():
        print(line)
    print(f"(activation estimate assumes batch_size={batch_size}, seq_len={seq_len}, optimizer={optimizer})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=str, default=None, help="Preset name (tiny/small/medium/xl_1b/1b) or a YAML path")
    parser.add_argument("--all", action="store_true", help="Report every built-in preset side by side")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--seq-len", type=int, default=None, help="Defaults to the config's own context_length")
    parser.add_argument("--optimizer", type=str, default="adamw", choices=["adamw", "sgd"])
    args = parser.parse_args()

    if not args.config and not args.all:
        raise SystemExit("Pass --config <name> or --all")

    names = PRESET_NAMES if args.all else [args.config]
    for name in names:
        config = load_model_config(_resolve_config_path(name))
        seq_len = args.seq_len if args.seq_len is not None else config.context_length
        _report(config, args.batch_size, seq_len, args.optimizer)

    print(EXPLANATION)


if __name__ == "__main__":
    main()
