"""Memory-mapped, sharded token dataset -- the on-disk counterpart to
`TokenizedDataset`'s in-memory path.

`TokenizedDataset` takes a token stream that is already a `torch.Tensor` in
RAM. That is the right shape for the corpora this project trained on when it
was CPU-only, and the wrong one for a multi-billion-token corpus: a 5B-token
stream is 40GB as int64, and `load_and_tokenize()` builds it by first holding
the entire corpus as one Python `str`.

This class instead reads the uint16 shards written by
`scripts/prepare_data.py` through `np.memmap`. Resident memory stays flat no
matter how large the corpus is -- the OS page cache serves the windows that
are actually touched and evicts the rest, so a 10GB corpus on a machine with
far less free RAM is a non-event. Tokens are widened uint16 -> int64 one
window at a time, in `__getitem__`, because that is the only place the model
needs them as int64.

Windows never straddle a shard boundary. A window that spanned two shards
would need both memmaps and a copy to join them, for a saving of at most
seq_len tokens per shard -- with 100M-token shards that is a rounding error,
and skipping it keeps indexing to one bisect and one slice.
"""

from __future__ import annotations

import bisect
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset


class ShardedTokenDataset(Dataset):
    """Fixed-length next-token-prediction windows over a set of uint16 shards.

    `stride` defaults to `seq_len` (disjoint windows -- one pass over the
    dataset really is one pass over the corpus), which is the opposite of
    `TokenizedDataset`'s default of 1. The default differs because the
    situations differ: overlapping windows are a cheap way to squeeze more
    examples out of a tiny corpus, and pure waste when the corpus is bigger
    than the training budget, which is the regime this class exists for.
    """

    def __init__(self, shard_paths: list[str | Path], seq_len: int, stride: int | None = None) -> None:
        if not shard_paths:
            raise ValueError("Need at least one shard")
        if stride is None:
            stride = seq_len
        if stride <= 0:
            raise ValueError(f"stride must be positive, got {stride}")

        self.seq_len = seq_len
        self.stride = stride
        self.shard_paths = [Path(p) for p in shard_paths]

        # mmap_mode="r" makes np.load return a read-only memmap rather than
        # reading the file: nothing is paged in until a window is indexed.
        self.shards = [np.load(p, mmap_mode="r") for p in self.shard_paths]

        # Windows per shard, and the running total, so __getitem__ can map a
        # global index to (shard, offset) with one bisect.
        self._counts = []
        for shard in self.shards:
            usable = shard.shape[0] - seq_len - 1
            self._counts.append(usable // stride + 1 if usable >= 0 else 0)

        self._cumulative = []
        running = 0
        for count in self._counts:
            running += count
            self._cumulative.append(running)
        self._length = running

        if self._length == 0:
            raise ValueError(
                f"No shard is long enough for a seq_len={seq_len} window "
                f"(shortest shard has {min(s.shape[0] for s in self.shards)} tokens)"
            )

    @classmethod
    def from_manifest(
        cls, manifest_path: str | Path, seq_len: int, split: str = "train", stride: int | None = None
    ) -> "ShardedTokenDataset":
        """Build from the manifest.json `scripts/prepare_data.py` writes.

        split="train" uses every shard but the first; split="val" uses only
        the first. That holdout is one contiguous shard rather than a random
        sample of windows on purpose -- see `_shard_path()` in prepare_data.py
        for why overlapping windows would otherwise leak training text into
        the validation measurement.
        """
        manifest_path = Path(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        directory = manifest_path.parent

        if split == "train":
            names = manifest["train_shards"]
        elif split == "val":
            names = [manifest["val_shard"]]
        else:
            raise ValueError(f"split must be 'train' or 'val', got {split!r}")

        if not names or names == [None]:
            raise ValueError(f"Manifest {manifest_path} lists no shards for split {split!r}")

        return cls([directory / name for name in names], seq_len=seq_len, stride=stride)

    @property
    def total_tokens(self) -> int:
        return sum(int(s.shape[0]) for s in self.shards)

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        if idx < 0:
            idx += self._length
        if not 0 <= idx < self._length:
            raise IndexError(f"index {idx} out of range for {self._length} windows")

        shard_idx = bisect.bisect_right(self._cumulative, idx)
        prior = self._cumulative[shard_idx - 1] if shard_idx > 0 else 0
        start = (idx - prior) * self.stride

        # np.asarray materializes this one window (and widens uint16 -> int64);
        # the rest of the shard stays on disk. torch.from_numpy then shares
        # that buffer rather than copying again.
        chunk = np.asarray(self.shards[shard_idx][start : start + self.seq_len + 1], dtype=np.int64)
        tensor = torch.from_numpy(chunk)
        return tensor[:-1], tensor[1:]  # input_ids, labels
