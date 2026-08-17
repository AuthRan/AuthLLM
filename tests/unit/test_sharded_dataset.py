"""Tests for the memory-mapped sharded dataset and the stride parameter.

The correctness property that matters most here is the same one
tests/unit/test_dataset.py asserts for the in-memory path: `labels` must be
`input_ids` shifted exactly one position, because that shift *is* the
next-token-prediction objective (see ashugpt/model/gpt.py). Everything else --
sharding, striding, memory-mapping -- is bookkeeping that must not disturb it.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import torch

from ashugpt.data import ShardedTokenDataset, TokenizedDataset


def write_shards(tmp_path, shard_lengths: list[int], start: int = 0):
    """Write uint16 shards holding consecutive counting numbers, so any window
    can be checked against what it should contain by arithmetic alone."""
    paths = []
    value = start
    for i, length in enumerate(shard_lengths):
        data = np.arange(value, value + length, dtype=np.uint16)
        path = tmp_path / f"shard_{i:03d}.npy"
        np.save(path, data)
        paths.append(path)
        value += length
    return paths


# ---- stride, on the in-memory dataset ----


def test_stride_one_is_unchanged_behavior() -> None:
    """stride=1 is the historical default and must stay exactly as it was:
    one window per starting position."""
    tokens = torch.arange(20)
    dataset = TokenizedDataset(tokens, seq_len=4)
    assert len(dataset) == 20 - 4
    assert dataset.stride == 1
    x, y = dataset[3]
    assert torch.equal(x, torch.arange(3, 7))
    assert torch.equal(y, torch.arange(4, 8))


def test_stride_produces_disjoint_windows() -> None:
    """stride=seq_len is what a real pretraining run wants: consecutive
    examples share no tokens, so one pass is one pass over the corpus."""
    tokens = torch.arange(21)
    dataset = TokenizedDataset(tokens, seq_len=4, stride=4)
    x0, _ = dataset[0]
    x1, _ = dataset[1]
    assert torch.equal(x0, torch.arange(0, 4))
    assert torch.equal(x1, torch.arange(4, 8))
    assert set(x0.tolist()).isdisjoint(x1.tolist())


def test_stride_never_reads_past_the_end() -> None:
    """__len__ must not promise a window the tensor cannot supply -- an
    off-by-one here yields short tensors and a shape error deep in the model."""
    for length in range(6, 40):
        for stride in (1, 2, 3, 4):
            dataset = TokenizedDataset(torch.arange(length), seq_len=4, stride=stride)
            for i in range(len(dataset)):
                x, y = dataset[i]
                assert x.numel() == 4 and y.numel() == 4, (length, stride, i)


def test_rejects_non_positive_stride() -> None:
    with pytest.raises(ValueError, match="stride must be positive"):
        TokenizedDataset(torch.arange(20), seq_len=4, stride=0)


# ---- the sharded dataset ----


def test_windows_have_the_one_token_shift(tmp_path) -> None:
    paths = write_shards(tmp_path, [100])
    dataset = ShardedTokenDataset(paths, seq_len=8)
    for i in range(len(dataset)):
        x, y = dataset[i]
        assert torch.equal(x[1:], y[:-1]), "labels must be input_ids shifted by one"


def test_defaults_to_disjoint_windows(tmp_path) -> None:
    """Opposite default from TokenizedDataset, on purpose: overlapping windows
    squeeze extra examples from a tiny corpus and are pure waste when the
    corpus is larger than the training budget."""
    paths = write_shards(tmp_path, [100])
    dataset = ShardedTokenDataset(paths, seq_len=10)
    assert dataset.stride == 10
    x0, _ = dataset[0]
    x1, _ = dataset[1]
    assert x0.tolist() == list(range(0, 10))
    assert x1.tolist() == list(range(10, 20))


def test_indexes_across_shard_boundaries(tmp_path) -> None:
    """A global index must resolve to the right (shard, offset) pair. The
    shards hold consecutive counting numbers, so the expected content of any
    window is known exactly."""
    paths = write_shards(tmp_path, [40, 40, 40])
    dataset = ShardedTokenDataset(paths, seq_len=8, stride=8)

    # 40 tokens per shard, seq_len 8, stride 8 -> 4 windows per shard
    # (a 5th would need token 40, which is in the next shard).
    assert len(dataset) == 12

    x, _ = dataset[0]  # shard 0, offset 0
    assert x.tolist() == list(range(0, 8))
    x, _ = dataset[4]  # shard 1, offset 0 -> values continue at 40
    assert x.tolist() == list(range(40, 48))
    x, _ = dataset[9]  # shard 2, offset 8 -> values 88..95
    assert x.tolist() == list(range(88, 96))


def test_windows_never_straddle_shards(tmp_path) -> None:
    """Documented invariant: a window is always drawn from one shard. Since
    each shard here is a contiguous counting run, a straddling window would
    show up as a non-consecutive step inside a single window."""
    paths = write_shards(tmp_path, [37, 37])
    dataset = ShardedTokenDataset(paths, seq_len=6, stride=3)
    for i in range(len(dataset)):
        x, _ = dataset[i]
        diffs = (x[1:] - x[:-1]).tolist()
        assert diffs == [1] * (x.numel() - 1), f"window {i} spans a discontinuity: {x.tolist()}"


def test_length_matches_enumerable_windows(tmp_path) -> None:
    paths = write_shards(tmp_path, [50, 31, 64])
    for seq_len in (4, 8, 16):
        for stride in (None, 1, 5, 16):
            dataset = ShardedTokenDataset(paths, seq_len=seq_len, stride=stride)
            count = 0
            for i in range(len(dataset)):
                x, y = dataset[i]
                assert x.numel() == seq_len and y.numel() == seq_len
                count += 1
            assert count == len(dataset)


def test_out_of_range_index_raises(tmp_path) -> None:
    paths = write_shards(tmp_path, [50])
    dataset = ShardedTokenDataset(paths, seq_len=8)
    with pytest.raises(IndexError):
        dataset[len(dataset)]


def test_negative_index_wraps(tmp_path) -> None:
    paths = write_shards(tmp_path, [50])
    dataset = ShardedTokenDataset(paths, seq_len=8)
    assert torch.equal(dataset[-1][0], dataset[len(dataset) - 1][0])


def test_returns_int64_regardless_of_uint16_storage(tmp_path) -> None:
    """Stored as uint16 to fit on disk, consumed as int64 because that is what
    an embedding lookup requires."""
    paths = write_shards(tmp_path, [50])
    x, y = ShardedTokenDataset(paths, seq_len=8)[0]
    assert x.dtype == torch.int64 and y.dtype == torch.int64


def test_does_not_read_shards_into_memory(tmp_path) -> None:
    """The point of this class: shards are memory-mapped, not loaded."""
    paths = write_shards(tmp_path, [1000])
    dataset = ShardedTokenDataset(paths, seq_len=8)
    assert isinstance(dataset.shards[0], np.memmap)


def test_rejects_shards_too_short_for_a_window(tmp_path) -> None:
    paths = write_shards(tmp_path, [4])
    with pytest.raises(ValueError, match="No shard is long enough"):
        ShardedTokenDataset(paths, seq_len=64)


def test_rejects_empty_shard_list() -> None:
    with pytest.raises(ValueError, match="at least one shard"):
        ShardedTokenDataset([], seq_len=8)


# ---- manifest loading ----


def build_manifest(tmp_path) -> "tuple":
    paths = write_shards(tmp_path, [60, 60, 60])
    renamed = []
    for i, path in enumerate(paths):
        name = "val_000000.npy" if i == 0 else f"train_{i - 1:06d}.npy"
        target = tmp_path / name
        path.rename(target)
        renamed.append(target)

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "dataset": "test",
                "total_tokens": 180,
                "val_shard": "val_000000.npy",
                "train_shards": ["train_000000.npy", "train_000001.npy"],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path, renamed


def test_from_manifest_splits_train_and_val(tmp_path) -> None:
    """The validation holdout is one contiguous shard, never a random sample of
    windows -- with overlapping windows a random split would leak training text
    into the held-out perplexity measurement."""
    manifest_path, _ = build_manifest(tmp_path)

    train = ShardedTokenDataset.from_manifest(manifest_path, seq_len=10, split="train")
    val = ShardedTokenDataset.from_manifest(manifest_path, seq_len=10, split="val")

    assert train.total_tokens == 120
    assert val.total_tokens == 60

    # The shards hold consecutive counting numbers, so disjointness is checkable
    # directly: val holds 0..59 and train holds 60..179.
    assert val[0][0][0].item() == 0
    assert train[0][0][0].item() == 60


def test_from_manifest_rejects_bad_split(tmp_path) -> None:
    manifest_path, _ = build_manifest(tmp_path)
    with pytest.raises(ValueError, match="split must be"):
        ShardedTokenDataset.from_manifest(manifest_path, seq_len=10, split="test")
