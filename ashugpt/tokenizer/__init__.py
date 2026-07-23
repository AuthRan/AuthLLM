"""BPE tokenizer: from-scratch byte-level trainer/encoder.

A tiktoken-backed production wrapper behind the same interface is deferred
to a later milestone (see SPEC.md).
"""

from ashugpt.tokenizer.bpe_scratch import BPETokenizer

__all__ = ["BPETokenizer"]
