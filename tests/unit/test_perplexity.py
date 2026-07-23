"""Unit tests for perplexity calculation and the validation loop."""

import math

import torch
from torch.utils.data import DataLoader

from ashugpt.config import ModelConfig
from ashugpt.data.dataset import TokenizedDataset
from ashugpt.eval.perplexity import evaluate, perplexity_from_loss
from ashugpt.model.gpt import AshuGPT


def test_perplexity_from_loss() -> None:
    assert perplexity_from_loss(0.0) == 1.0
    assert math.isclose(perplexity_from_loss(math.log(2)), 2.0, rel_tol=1e-9)


def small_model_config(vocab_size: int = 32) -> ModelConfig:
    return ModelConfig(
        name="test", vocab_size=vocab_size, d_model=16, n_layers=2, n_heads=2, n_kv_heads=2, d_ff=32, context_length=16
    )


def test_evaluate_matches_manual_average_loss() -> None:
    torch.manual_seed(0)
    config = small_model_config()
    model = AshuGPT(config)

    tokens = torch.randint(0, config.vocab_size, (200,))
    loader = DataLoader(TokenizedDataset(tokens, seq_len=8), batch_size=4, shuffle=False)

    metrics = evaluate(model, loader, amp_dtype=None, max_batches=5)

    model.eval()
    manual_losses = []
    with torch.no_grad():
        for i, (input_ids, labels) in enumerate(loader):
            if i >= 5:
                break
            manual_losses.append(model(input_ids, labels=labels).loss.item())
    expected_loss = sum(manual_losses) / len(manual_losses)

    assert math.isclose(metrics["loss"], expected_loss, rel_tol=1e-5)
    assert math.isclose(metrics["perplexity"], math.exp(expected_loss), rel_tol=1e-5)


def test_evaluate_respects_max_batches() -> None:
    torch.manual_seed(0)
    config = small_model_config()
    model = AshuGPT(config)
    tokens = torch.randint(0, config.vocab_size, (500,))
    loader = DataLoader(TokenizedDataset(tokens, seq_len=8), batch_size=4, shuffle=False)

    # Just confirming this doesn't error and returns a well-formed result
    # when far fewer batches are requested than exist.
    metrics = evaluate(model, loader, amp_dtype=None, max_batches=2)
    assert math.isfinite(metrics["loss"])
    assert metrics["perplexity"] > 1.0


def test_evaluate_leaves_model_in_eval_mode() -> None:
    torch.manual_seed(0)
    config = small_model_config()
    model = AshuGPT(config)
    model.train()
    tokens = torch.randint(0, config.vocab_size, (100,))
    loader = DataLoader(TokenizedDataset(tokens, seq_len=8), batch_size=4, shuffle=False)

    evaluate(model, loader, amp_dtype=None)
    assert not model.training


def test_untrained_model_perplexity_is_near_vocab_size() -> None:
    # A randomly initialized model's predictions are close to uniform, so
    # cross-entropy loss should be close to ln(vocab_size), and perplexity
    # close to vocab_size itself.
    torch.manual_seed(0)
    config = small_model_config(vocab_size=64)
    model = AshuGPT(config)
    tokens = torch.randint(0, config.vocab_size, (500,))
    loader = DataLoader(TokenizedDataset(tokens, seq_len=16), batch_size=8, shuffle=False)

    metrics = evaluate(model, loader, amp_dtype=None)
    assert 0.5 * config.vocab_size < metrics["perplexity"] < 1.5 * config.vocab_size
