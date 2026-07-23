"""Data pipeline: tokenized-dataset loading, chunking, and splitting.

download.py (fetching a large real corpus like TinyStories) remains
unimplemented -- this milestone tokenizes a local text file directly, which
is all a small CPU-scale corpus needs.
"""

from ashugpt.data.dataset import TokenizedDataset, load_and_tokenize, split_train_val

__all__ = ["TokenizedDataset", "load_and_tokenize", "split_train_val"]
