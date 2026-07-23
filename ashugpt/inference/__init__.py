"""Generation (sampling + the autoregressive decoding loop) and loading
public pretrained checkpoints for inference (never for training -- see
pretrained_loader.py's module docstring for why GPT-2 specifically cannot
be loaded, and README.md for the implemented/trained/pretrained distinction).

The clean inference API class remains unimplemented -- see SPEC.md M14.
"""

from ashugpt.inference.generate import generate, generate_text, sample_next_token
from ashugpt.inference.pretrained_loader import (
    CompatibilityReport,
    IncompatibleArchitectureError,
    convert_gpt2_state_dict,
    gpt2_config_to_model_config,
    load_gpt2_checkpoint,
)

__all__ = [
    "generate",
    "generate_text",
    "sample_next_token",
    "CompatibilityReport",
    "IncompatibleArchitectureError",
    "convert_gpt2_state_dict",
    "gpt2_config_to_model_config",
    "load_gpt2_checkpoint",
]
