"""Unit tests for the AshuGPT configuration system."""

from pathlib import Path

import pytest

from ashugpt.config import ModelConfig, TrainConfig, load_model_config

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "model"
PRESETS = ["tiny", "small", "medium", "xl_1b"]


def base_train_config(**overrides) -> TrainConfig:
    defaults = dict(batch_size=2, seq_len=8, max_steps=100, warmup_steps=10, max_lr=1e-3, min_lr=1e-4)
    defaults.update(overrides)
    return TrainConfig(**defaults)


@pytest.mark.parametrize("preset", PRESETS)
def test_preset_loads(preset: str) -> None:
    config = load_model_config(CONFIGS_DIR / f"{preset}.yaml")
    assert isinstance(config, ModelConfig)
    assert config.name == preset


def test_head_dim() -> None:
    config = load_model_config(CONFIGS_DIR / "small.yaml")
    assert config.head_dim == config.d_model // config.n_heads


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError):
        ModelConfig(
            name="broken",
            vocab_size=1000,
            d_model=100,
            n_layers=2,
            n_heads=3,  # 100 is not divisible by 3
            n_kv_heads=3,
            d_ff=256,
            context_length=64,
        )


def test_param_counts_scale_in_order() -> None:
    counts = [
        load_model_config(CONFIGS_DIR / f"{preset}.yaml").approx_param_count()
        for preset in PRESETS
    ]
    assert counts == sorted(counts)
    assert counts[-1] > 1_000_000_000  # xl_1b should be 1B+


# ---- TrainConfig: memory optimization fields (Milestone 9) ----


def test_train_config_memory_optimization_defaults_are_off() -> None:
    config = base_train_config()
    assert config.gradient_checkpointing is False
    assert config.use_efficient_attention is False
    assert config.optimizer == "adamw"


def test_train_config_accepts_memory_optimization_overrides() -> None:
    config = base_train_config(gradient_checkpointing=True, use_efficient_attention=True, optimizer="sgd")
    assert config.gradient_checkpointing is True
    assert config.use_efficient_attention is True
    assert config.optimizer == "sgd"


def test_train_config_rejects_unknown_optimizer() -> None:
    with pytest.raises(ValueError):
        base_train_config(optimizer="rmsprop")
