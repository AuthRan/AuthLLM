"""Tokenized-dataset handling for pretraining.

A whole corpus is tokenized once into one flat 1D stream of token ids, then
sliced into fixed-length overlapping windows for next-token prediction.
This keeps the pipeline as an in-memory tensor rather than the on-disk
memmap shards SPEC.md originally sketched for a much larger corpus -- at
the current CPU / small-corpus scale that's unnecessary complexity, so it's
deferred until a real multi-GB corpus actually needs streaming.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

from ashugpt.tokenizer import BPETokenizer


def load_and_tokenize(
    path: str | Path, tokenizer: BPETokenizer, cache_path: str | Path | None = None
) -> torch.Tensor:
    """Read a UTF-8 text file and tokenize it into one flat 1D LongTensor
    of token ids -- the "dataset loading" step for pretraining.

    Tokenizing a multi-hundred-MB corpus is the slow part of a run, and on
    ephemeral machines (Kaggle/Colab sessions restart) it would otherwise be
    re-paid every session. If `cache_path` is given, the token stream is
    saved there as a uint16 .npy array on first run (vocab_size < 65536, so
    uint16 holds every id at 1/4 the memory of int64) and memory-mapped back
    on later runs, skipping tokenization entirely."""
    if cache_path is not None and Path(cache_path).exists():
        ids = np.load(cache_path, mmap_mode="r")
        return torch.from_numpy(np.asarray(ids, dtype=np.int64))

    text = Path(path).read_text(encoding="utf-8")
    ids = tokenizer.encode(text)

    if cache_path is not None:
        if tokenizer.vocab_size > np.iinfo(np.uint16).max + 1:
            raise ValueError(
                f"vocab_size ({tokenizer.vocab_size}) exceeds uint16 range; widen the cache dtype"
            )
        np.save(cache_path, np.asarray(ids, dtype=np.uint16))

    return torch.tensor(ids, dtype=torch.long)


def split_train_val(token_ids: torch.Tensor, val_fraction: float = 0.1) -> tuple[torch.Tensor, torch.Tensor]:
    """Split one token stream into a leading train portion and a trailing
    held-out validation portion (no shuffling -- shuffling token *order*
    within a single running text would let validation windows leak
    context from immediately-adjacent training windows)."""
    if not 0.0 < val_fraction < 1.0:
        raise ValueError(f"val_fraction must be in (0, 1), got {val_fraction}")
    split_idx = int(token_ids.numel() * (1.0 - val_fraction))
    return token_ids[:split_idx], token_ids[split_idx:]


class TokenizedDataset(Dataset):
    """Slices a flat token stream into fixed-length (seq_len) windows for
    next-token prediction -- one window per starting position, so
    consecutive indices overlap almost entirely (a "sliding window"). Each
    __getitem__ call is just a cheap tensor slice; no window is
    precomputed or copied up front.

        input_ids[i] = tokens[i : i+seq_len]
        labels[i]    = tokens[i+1 : i+seq_len+1]     -- input_ids shifted by one

    See ashugpt/model/gpt.py's docstring for why this shift is exactly the
    next-token-prediction target the model expects.

    `stride` controls how far apart consecutive windows start. The default of
    1 is the original behavior (every starting position is its own example),
    which is what the small-corpus CPU runs used and what the tests assume.
    It is also enormously redundant at scale: with stride=1 two consecutive
    examples share seq_len-1 of their seq_len tokens, so one "epoch" revisits
    essentially the same text seq_len times over. Setting stride=seq_len gives
    disjoint windows -- one pass really is one pass over the corpus -- which is
    what a real pretraining run wants. See TrainConfig.stride.
    """

    def __init__(self, token_ids: torch.Tensor, seq_len: int, stride: int = 1) -> None:
        if token_ids.numel() < seq_len + 1:
            raise ValueError(
                f"Need at least {seq_len + 1} tokens to form one training example, got {token_ids.numel()}"
            )
        if stride <= 0:
            raise ValueError(f"stride must be positive, got {stride}")
        self.token_ids = token_ids
        self.seq_len = seq_len
        self.stride = stride

    def __len__(self) -> int:
        # Last valid window start is numel - seq_len - 1 (a window needs
        # seq_len + 1 tokens: seq_len inputs plus the final label).
        return (self.token_ids.numel() - self.seq_len - 1) // self.stride + 1

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        start = idx * self.stride
        chunk = self.token_ids[start : start + self.seq_len + 1]  # one slice, length seq_len + 1
        return chunk[:-1], chunk[1:]  # input_ids, labels
