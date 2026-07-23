"""Unit tests for tokenized-dataset loading, chunking, and splitting."""

from pathlib import Path

import pytest
import torch

from ashugpt.data.dataset import TokenizedDataset, load_and_tokenize, split_train_val
from ashugpt.tokenizer import BPETokenizer

CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_corpus.txt"


@pytest.fixture(scope="module")
def tokenizer() -> BPETokenizer:
    text = CORPUS_PATH.read_text(encoding="utf-8")
    return BPETokenizer.train(text, vocab_size=300)


def test_load_and_tokenize_matches_direct_encode(tokenizer: BPETokenizer) -> None:
    token_ids = load_and_tokenize(CORPUS_PATH, tokenizer)
    expected = tokenizer.encode(CORPUS_PATH.read_text(encoding="utf-8"))
    assert token_ids.dtype == torch.long
    assert token_ids.tolist() == expected


# ---- TokenizedDataset: chunking + shift ----


def test_length_is_num_tokens_minus_seq_len() -> None:
    tokens = torch.arange(100)
    ds = TokenizedDataset(tokens, seq_len=10)
    assert len(ds) == 90


def test_getitem_produces_the_one_token_shift() -> None:
    tokens = torch.arange(20)  # [0, 1, 2, ..., 19]
    ds = TokenizedDataset(tokens, seq_len=5)

    input_ids, labels = ds[0]
    assert input_ids.tolist() == [0, 1, 2, 3, 4]
    assert labels.tolist() == [1, 2, 3, 4, 5]  # input_ids shifted by one

    input_ids, labels = ds[3]
    assert input_ids.tolist() == [3, 4, 5, 6, 7]
    assert labels.tolist() == [4, 5, 6, 7, 8]


def test_consecutive_windows_overlap() -> None:
    tokens = torch.arange(20)
    ds = TokenizedDataset(tokens, seq_len=5)
    input_0, _ = ds[0]
    input_1, _ = ds[1]
    assert input_0[1:].tolist() == input_1[:-1].tolist()


def test_last_valid_index_stays_in_bounds() -> None:
    tokens = torch.arange(20)
    ds = TokenizedDataset(tokens, seq_len=5)
    last = len(ds) - 1
    input_ids, labels = ds[last]
    assert len(input_ids) == 5
    assert len(labels) == 5
    assert labels[-1].item() == 19  # the final token in the stream


def test_rejects_too_few_tokens() -> None:
    tokens = torch.arange(5)
    with pytest.raises(ValueError):
        TokenizedDataset(tokens, seq_len=10)


def test_dataloader_batches_have_expected_shape() -> None:
    from torch.utils.data import DataLoader

    tokens = torch.arange(200)
    ds = TokenizedDataset(tokens, seq_len=8)
    loader = DataLoader(ds, batch_size=4, shuffle=True)
    input_ids, labels = next(iter(loader))
    assert input_ids.shape == (4, 8)
    assert labels.shape == (4, 8)


# ---- train/val split ----


def test_split_train_val_sizes() -> None:
    tokens = torch.arange(1000)
    train_ids, val_ids = split_train_val(tokens, val_fraction=0.2)
    assert train_ids.numel() == 800
    assert val_ids.numel() == 200
    assert torch.equal(torch.cat([train_ids, val_ids]), tokens)  # no tokens lost or reordered


def test_split_train_val_rejects_invalid_fraction() -> None:
    tokens = torch.arange(100)
    with pytest.raises(ValueError):
        split_train_val(tokens, val_fraction=0.0)
    with pytest.raises(ValueError):
        split_train_val(tokens, val_fraction=1.0)
