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

import torch
from torch.utils.data import Dataset

from ashugpt.tokenizer import BPETokenizer


def load_and_tokenize(path: str | Path, tokenizer: BPETokenizer) -> torch.Tensor:
    """Read a UTF-8 text file and tokenize it into one flat 1D LongTensor
    of token ids -- the "dataset loading" step for pretraining."""
    text = Path(path).read_text(encoding="utf-8")
    ids = tokenizer.encode(text)
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
    """

    def __init__(self, token_ids: torch.Tensor, seq_len: int) -> None:
        if token_ids.numel() < seq_len + 1:
            raise ValueError(
                f"Need at least {seq_len + 1} tokens to form one training example, got {token_ids.numel()}"
            )
        self.token_ids = token_ids
        self.seq_len = seq_len

    def __len__(self) -> int:
        return self.token_ids.numel() - self.seq_len

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        chunk = self.token_ids[idx : idx + self.seq_len + 1]  # one slice, length seq_len + 1
        return chunk[:-1], chunk[1:]  # input_ids, labels
