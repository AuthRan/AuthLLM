"""Generation: sampling strategies and the autoregressive decoding loop.

KV cache, the clean inference API class, and loading public pretrained
checkpoints remain unimplemented -- see SPEC.md milestones M10, M13-M14.
"""

from ashugpt.inference.generate import generate, generate_text, sample_next_token

__all__ = ["generate", "generate_text", "sample_next_token"]
