"""BPE tokenizers.

Two implementations behind one interface, as SPEC.md's "Tokenizer: hybrid"
constraint intended:

- `BPETokenizer` (`bpe_scratch.py`) -- the from-scratch byte-level trainer and
  encoder. The pedagogical artifact, and the one that is unit-tested against
  known-correct behavior.
- `TiktokenBPETokenizer` (`tiktoken_bpe.py`) -- GPT-2's published merge table
  via tiktoken. What real training runs use, because the from-scratch trainer
  is O(vocab_size x corpus) and does not scale to a 50k-merge vocab over a
  multi-GB corpus.

Both expose the same attributes (`vocab_size`, `pad_id`, `bos_id`, `eos_id`,
`unk_id`) and methods (`encode`, `decode`, `encode_batch`, `save`, `load`),
so everything downstream -- dataset preparation, training, generation, the
API -- is written against the interface and never against either class.

Their *id spaces* differ, though, so a checkpoint must be paired with the
tokenizer it was trained with. `load_tokenizer()` reads which one a saved
file describes and returns it, which is why scripts call that rather than a
concrete class.
"""

from pathlib import Path

from ashugpt.tokenizer.bpe_scratch import BPETokenizer
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer

__all__ = ["BPETokenizer", "TiktokenBPETokenizer", "load_tokenizer"]


def load_tokenizer(path: str | Path) -> BPETokenizer | TiktokenBPETokenizer:
    """Load whichever tokenizer a saved file describes.

    Files written by `TiktokenBPETokenizer.save()` carry `"type": "tiktoken"`;
    from-scratch files predate that field and are identified by its absence,
    so tokenizer files saved before this module existed still load correctly.
    """
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("type") == "tiktoken":
        return TiktokenBPETokenizer.load(path)
    return BPETokenizer.load(path)
