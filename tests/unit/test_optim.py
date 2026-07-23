"""Unit tests for the optimizer factory and LR schedule."""

import math

import pytest
import torch

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.model.gpt import AshuGPT
from ashugpt.training.optim import build_optimizer, get_lr


def small_model_config() -> ModelConfig:
    return ModelConfig(
        name="test", vocab_size=32, d_model=16, n_layers=2, n_heads=2, n_kv_heads=2, d_ff=32, context_length=16
    )


def base_train_config(**overrides) -> TrainConfig:
    defaults = dict(batch_size=2, seq_len=8, max_steps=100, warmup_steps=10, max_lr=1e-3, min_lr=1e-4)
    defaults.update(overrides)
    return TrainConfig(**defaults)


# ---- optimizer ----


def test_optimizer_splits_decay_and_no_decay_params() -> None:
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.1)

    assert len(optimizer.param_groups) == 2
    decay_group = next(g for g in optimizer.param_groups if g["weight_decay"] > 0)
    no_decay_group = next(g for g in optimizer.param_groups if g["weight_decay"] == 0)

    assert all(p.dim() >= 2 for p in decay_group["params"])
    assert all(p.dim() < 2 for p in no_decay_group["params"])
    # RMSNorm weights (1D) must land in the no-decay group.
    norm_weight_numel = model.blocks[0].attn_norm.weight.numel()
    assert any(p.numel() == norm_weight_numel and p.dim() == 1 for p in no_decay_group["params"])


def test_optimizer_covers_every_parameter() -> None:
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.1)
    optimized_params = sum(p.numel() for g in optimizer.param_groups for p in g["params"])
    assert optimized_params == model.num_parameters()


# ---- optimizer choice / memory footprint (Milestone 9) ----


def test_build_optimizer_sgd_returns_sgd_with_momentum() -> None:
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.1, betas=(0.9, 0.95), optimizer="sgd")
    assert isinstance(optimizer, torch.optim.SGD)
    assert optimizer.param_groups[0]["momentum"] == 0.9


def test_build_optimizer_rejects_unknown_name() -> None:
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    with pytest.raises(ValueError):
        build_optimizer(model, lr=1e-3, weight_decay=0.1, optimizer="rmsprop")


def test_adamw_keeps_twice_the_optimizer_state_sgd_does() -> None:
    # The core memory claim for optimizer choice: AdamW keeps exp_avg AND
    # exp_avg_sq (2 full-size buffers per parameter); SGD+momentum keeps
    # only momentum_buffer (1). State is lazily allocated on the first
    # step(), so this only shows up after actually stepping once.
    config = small_model_config()
    x = torch.randint(0, config.vocab_size, (2, 8))
    y = torch.randint(0, config.vocab_size, (2, 8))

    def state_numel(optimizer: torch.optim.Optimizer, keys: list[str]) -> int:
        return sum(
            state[k].numel() for state in optimizer.state.values() for k in keys if torch.is_tensor(state.get(k))
        )

    torch.manual_seed(0)
    model_adamw = AshuGPT(config)
    opt_adamw = build_optimizer(model_adamw, lr=1e-3, weight_decay=0.0, optimizer="adamw")
    model_adamw(x, labels=y).loss.backward()
    opt_adamw.step()

    torch.manual_seed(0)
    model_sgd = AshuGPT(config)
    opt_sgd = build_optimizer(model_sgd, lr=1e-3, weight_decay=0.0, optimizer="sgd")
    model_sgd(x, labels=y).loss.backward()
    opt_sgd.step()

    param_count = model_adamw.num_parameters()
    assert state_numel(opt_adamw, ["exp_avg", "exp_avg_sq"]) == 2 * param_count
    assert state_numel(opt_sgd, ["momentum_buffer"]) == param_count


# ---- LR schedule ----


def test_lr_ramps_up_linearly_during_warmup() -> None:
    config = base_train_config(warmup_steps=10, max_lr=1e-3)
    lr_0 = get_lr(0, config)
    lr_5 = get_lr(5, config)
    lr_9 = get_lr(9, config)
    assert lr_0 < lr_5 < lr_9
    assert lr_0 == config.max_lr * (1 / 10)


def test_lr_peaks_at_end_of_warmup() -> None:
    config = base_train_config(warmup_steps=10, max_lr=1e-3)
    assert math.isclose(get_lr(10, config), config.max_lr, rel_tol=1e-6)


def test_lr_decays_after_warmup() -> None:
    config = base_train_config(warmup_steps=10, max_steps=100, max_lr=1e-3, min_lr=1e-5)
    lr_mid = get_lr(50, config)
    lr_late = get_lr(90, config)
    assert config.min_lr < lr_late < lr_mid < config.max_lr


def test_lr_floors_at_min_lr_past_max_steps() -> None:
    config = base_train_config(warmup_steps=10, max_steps=100, min_lr=1e-5)
    assert get_lr(100, config) == config.min_lr
    assert get_lr(1000, config) == config.min_lr
