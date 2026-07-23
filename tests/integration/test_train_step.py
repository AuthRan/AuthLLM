"""Integration test: prove the training loop can actually learn.

Unlike the unit tests (which check each piece in isolation), this trains a
real tiny model on a real tiny dataset end to end and checks the one thing
none of the unit tests can: that gradient descent through this whole stack
(data -> model -> loss -> backward -> optimizer -> scheduler) actually
drives the loss down. It's intentionally slower than the rest of the unit
suite, which is why it lives under tests/integration/ rather than
tests/unit/.
"""

from pathlib import Path

import torch

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.data.dataset import TokenizedDataset, load_and_tokenize
from ashugpt.model.gpt import AshuGPT
from ashugpt.tokenizer import BPETokenizer
from ashugpt.training.trainer import train

SYNTHETIC_CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_corpus.txt"


def test_tiny_model_overfits_a_tiny_synthetic_dataset() -> None:
    # The synthetic corpus is deliberately just four short template
    # sentences repeated many times -- small vocabulary, highly
    # predictable structure, so a tiny model can memorize it in well under
    # a minute on CPU. That's the point: this test isn't about quality of
    # generated text, it's a fast, reliable proof that the whole pipeline
    # (chunking -> batching -> forward -> loss -> backward -> optimizer ->
    # scheduler) is wired together correctly and gradient descent actually
    # reduces the loss.
    text = SYNTHETIC_CORPUS_PATH.read_text(encoding="utf-8")
    tokenizer = BPETokenizer.train(text, vocab_size=300)

    model_config = ModelConfig(
        name="overfit-test",
        vocab_size=tokenizer.vocab_size,
        d_model=64,
        n_layers=2,
        n_heads=4,
        n_kv_heads=4,
        d_ff=128,
        context_length=32,
    )
    train_config = TrainConfig(
        batch_size=8,
        seq_len=32,
        max_steps=150,
        warmup_steps=10,
        max_lr=5e-3,
        min_lr=5e-4,
        log_interval=1,  # record every step, so we can compare the start and end of the run
        eval_interval=10**9,  # no held-out set for this test -- effectively disables periodic eval
        checkpoint_interval=10**9,  # effectively disables checkpointing for this test
        amp_dtype="bfloat16",  # exercise the realistic default training path, not just fp32
        seed=0,
    )

    token_ids = load_and_tokenize(SYNTHETIC_CORPUS_PATH, tokenizer)
    train_dataset = TokenizedDataset(token_ids, seq_len=train_config.seq_len)

    torch.manual_seed(0)
    model = AshuGPT(model_config)

    history = train(model, train_dataset, val_dataset=None, config=train_config, model_config=model_config)

    train_losses = [row["train_loss"] for row in history if "train_loss" in row]
    assert len(train_losses) == train_config.max_steps

    initial_loss = sum(train_losses[:5]) / 5
    final_loss = sum(train_losses[-5:]) / 5

    # A randomly initialized model should start near ln(vocab_size); after
    # 150 steps of overfitting this tiny repetitive corpus it should have
    # collapsed dramatically -- in practice it drops from ~5.5 to well
    # under 0.1, so this threshold leaves a wide, non-flaky safety margin.
    assert final_loss < 0.25 * initial_loss
    assert final_loss < 1.0
