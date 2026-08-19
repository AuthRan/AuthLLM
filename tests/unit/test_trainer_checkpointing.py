"""Unit tests for which steps the training loop writes a checkpoint at."""

from pathlib import Path

import torch
from torch.utils.data import Dataset

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.model import AshuGPT
from ashugpt.training.trainer import train


class _RandomTokens(Dataset):
    """Enough batches to run a handful of steps; the loss is irrelevant here."""

    def __init__(self, n: int, seq_len: int, vocab_size: int) -> None:
        generator = torch.Generator().manual_seed(0)
        self.tokens = torch.randint(0, vocab_size, (n, seq_len + 1), generator=generator)

    def __len__(self) -> int:
        return len(self.tokens)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        row = self.tokens[index]
        return row[:-1], row[1:]


def _run(tmp_path: Path, max_steps: int, checkpoint_interval: int) -> list[int]:
    model_config = ModelConfig(
        name="ckpt-test",
        vocab_size=64,
        d_model=32,
        n_layers=1,
        n_heads=2,
        n_kv_heads=2,
        d_ff=64,
        context_length=16,
    )
    train_config = TrainConfig(
        batch_size=2,
        seq_len=16,
        max_steps=max_steps,
        warmup_steps=1,
        max_lr=1e-3,
        min_lr=1e-4,
        log_interval=10**9,
        eval_interval=10**9,
        checkpoint_interval=checkpoint_interval,
        amp_dtype="none",
        num_workers=0,
        seed=0,
    )

    torch.manual_seed(0)
    train(
        AshuGPT(model_config),
        _RandomTokens(16, train_config.seq_len, model_config.vocab_size),
        val_dataset=None,
        config=train_config,
        model_config=model_config,
        checkpoint_dir=tmp_path,
    )
    return sorted(int(p.stem.removeprefix("step_")) for p in tmp_path.glob("step_*.pt"))


def test_final_step_is_checkpointed_even_off_the_interval(tmp_path: Path) -> None:
    """A run that ends between intervals must still save the weights it ended with.

    Without this the last file on disk is from an earlier step, and the
    final loss and validation numbers the run reported belong to a model
    that no longer exists anywhere.
    """
    assert _run(tmp_path, max_steps=5, checkpoint_interval=2) == [2, 4, 5]


def test_no_duplicate_checkpoint_when_the_run_ends_on_an_interval(tmp_path: Path) -> None:
    assert _run(tmp_path, max_steps=4, checkpoint_interval=2) == [2, 4]
