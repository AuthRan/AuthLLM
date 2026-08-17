"""Data pipeline: tokenized-dataset loading, chunking, and splitting.

Two paths, for two scales:

- `load_and_tokenize` + `TokenizedDataset` -- one text file tokenized into
  one in-memory tensor. Simple, and adequate for the small corpora this
  project trained on while it was CPU-only.
- `ShardedTokenDataset` -- memory-mapped uint16 shards produced by
  `scripts/prepare_data.py`. What a multi-billion-token corpus needs, since
  the in-memory path would hold the whole corpus as a Python `str` and then
  again as an int64 tensor.
"""

from ashugpt.data.dataset import TokenizedDataset, load_and_tokenize, split_train_val
from ashugpt.data.sharded import ShardedTokenDataset

__all__ = ["TokenizedDataset", "ShardedTokenDataset", "load_and_tokenize", "split_train_val"]
