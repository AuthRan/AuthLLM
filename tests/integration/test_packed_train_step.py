"""Integration test: the packed path learns, end to end, through the real trainer.

`tests/unit/test_packed_instruction_dataset.py` proves a packed window produces
the same logits as its examples run alone. That is the correctness property,
but it is a statement about one forward pass. This test checks the other half:
that the four-tensor batches the packed dataset emits actually survive the
DataLoader, the trainer's batch splitting, autocast, and the optimizer, and
that gradient descent through the whole stack still drives the loss down.

The failure this exists to catch is plumbing, not math -- a trainer that
silently dropped segment_ids would still run, still converge on this easy
corpus, and differ from the unpacked path only in ways no assertion here would
see. So it also asserts the batch arrives with all four tensors and that the
packing actually packed something, which is what makes the rest meaningful.
"""

from __future__ import annotations

import torch
from torch.utils.data import DataLoader

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.data.batch import split_batch
from ashugpt.data.instruction import InstructionExample, PackedInstructionDataset
from ashugpt.model.gpt import AshuGPT
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training.trainer import train


def _examples(n: int) -> list[InstructionExample]:
    """A tiny, highly repetitive instruction set -- the point is that a small
    model can memorize it fast, not that the task is interesting."""
    pairs = [
        ("Name a colour.", "Blue."),
        ("Name a number.", "Seven."),
        ("Name an animal.", "Otter."),
        ("Name a fruit.", "Plum."),
    ]
    return [InstructionExample(q, "", a) for q, a in pairs * n]


def test_packed_batches_carry_all_four_tensors() -> None:
    """If the loader or split_batch dropped the extras, training would still
    run -- as plain causal attention over a concatenated window."""
    tokenizer = TiktokenBPETokenizer()
    dataset = PackedInstructionDataset(_examples(8), tokenizer, seq_len=128)
    assert len(dataset) < 32, "test needs windows that actually hold several examples"

    loader = DataLoader(dataset, batch_size=2)
    batch = next(iter(loader))
    assert len(batch) == 4

    input_ids, labels, extras = split_batch(batch, torch.device("cpu"))
    assert set(extras) == {"segment_ids", "position_ids"}
    assert extras["segment_ids"].shape == input_ids.shape
    assert extras["position_ids"].shape == labels.shape
    # More than one example per window, or the mask is doing nothing.
    assert extras["segment_ids"].max().item() > 0


def test_training_on_packed_windows_reduces_the_loss() -> None:
    tokenizer = TiktokenBPETokenizer()
    dataset = PackedInstructionDataset(_examples(24), tokenizer, seq_len=128)

    model_config = ModelConfig(
        name="packed-overfit-test",
        vocab_size=tokenizer.vocab_size,
        d_model=64,
        n_layers=2,
        n_heads=4,
        n_kv_heads=4,
        d_ff=128,
        context_length=128,
    )
    train_config = TrainConfig(
        batch_size=4,
        seq_len=128,
        max_steps=60,
        warmup_steps=5,
        max_lr=3e-3,
        min_lr=3e-4,
        log_interval=1,
        eval_interval=10**9,
        checkpoint_interval=10**9,
        amp_dtype="none",  # keep the assertion about loss magnitude deterministic
        pack_sequences=True,
        seed=0,
    )

    torch.manual_seed(0)
    model = AshuGPT(model_config)
    history = train(model, dataset, val_dataset=None, config=train_config, model_config=model_config)

    losses = [row["train_loss"] for row in history if "train_loss" in row]
    assert len(losses) == train_config.max_steps

    initial = sum(losses[:5]) / 5
    final = sum(losses[-5:]) / 5
    assert final < 0.5 * initial, f"loss did not fall: {initial:.3f} -> {final:.3f}"
