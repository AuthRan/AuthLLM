"""Unit tests for the memory estimator (Milestone 10)."""

import time

import pytest

from ashugpt.config import ModelConfig
from ashugpt.utils.memory import (
    activation_memory_bytes,
    estimate_memory,
    gradient_memory_bytes,
    optimizer_state_memory_bytes,
    weight_memory_bytes,
)


def small_config() -> ModelConfig:
    return ModelConfig(
        name="test", vocab_size=1000, d_model=128, n_layers=4, n_heads=4, n_kv_heads=4, d_ff=384, context_length=256
    )


# ---- individual component formulas ----


def test_weight_memory_bf16_is_half_of_fp32() -> None:
    assert weight_memory_bytes(1_000_000, "bf16") == weight_memory_bytes(1_000_000, "fp32") // 2


def test_weight_memory_scales_linearly_with_param_count() -> None:
    assert weight_memory_bytes(2_000_000, "fp32") == 2 * weight_memory_bytes(1_000_000, "fp32")


def test_gradient_memory_matches_fp32_weight_memory() -> None:
    assert gradient_memory_bytes(1_000_000) == weight_memory_bytes(1_000_000, "fp32")


def test_optimizer_state_adamw_is_twice_sgd() -> None:
    assert optimizer_state_memory_bytes(1_000_000, "adamw") == 2 * optimizer_state_memory_bytes(1_000_000, "sgd")


def test_optimizer_state_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        optimizer_state_memory_bytes(1000, "rmsprop")


def test_activation_memory_scales_linearly_with_batch_size() -> None:
    config = small_config()
    small_batch = activation_memory_bytes(config, batch_size=1, seq_len=64)
    big_batch = activation_memory_bytes(config, batch_size=4, seq_len=64)
    assert big_batch == 4 * small_batch


def test_activation_memory_grows_faster_than_linear_with_seq_len() -> None:
    # The attention score matrix is O(seq_len^2), so doubling seq_len
    # should more than double total activation memory once seq_len is
    # large enough for that term to dominate the linear ones.
    config = small_config()
    short = activation_memory_bytes(config, batch_size=1, seq_len=512)
    long = activation_memory_bytes(config, batch_size=1, seq_len=1024)
    assert long > 2 * short


def test_activation_memory_bf16_is_half_of_fp32() -> None:
    config = small_config()
    fp32 = activation_memory_bytes(config, batch_size=2, seq_len=64, dtype="fp32")
    bf16 = activation_memory_bytes(config, batch_size=2, seq_len=64, dtype="bf16")
    assert bf16 == fp32 // 2


# ---- estimate_memory end to end ----


def test_estimate_memory_components_sum_to_total() -> None:
    config = small_config()
    estimate = estimate_memory(config, batch_size=2, seq_len=64)
    assert estimate.total_training_memory == (
        estimate.weight_memory_fp32 + estimate.gradient_memory + estimate.optimizer_memory + estimate.activation_memory
    )


def test_estimate_memory_defaults_seq_len_to_context_length() -> None:
    config = small_config()
    default_estimate = estimate_memory(config, batch_size=1)
    explicit_estimate = estimate_memory(config, batch_size=1, seq_len=config.context_length)
    assert default_estimate.activation_memory == explicit_estimate.activation_memory


def test_estimate_memory_param_count_matches_config() -> None:
    config = small_config()
    estimate = estimate_memory(config)
    assert estimate.param_count == config.approx_param_count()


def test_estimate_memory_sgd_uses_less_optimizer_memory_than_adamw() -> None:
    config = small_config()
    adamw_estimate = estimate_memory(config, optimizer="adamw")
    sgd_estimate = estimate_memory(config, optimizer="sgd")
    assert sgd_estimate.optimizer_memory < adamw_estimate.optimizer_memory
    assert sgd_estimate.total_training_memory < adamw_estimate.total_training_memory


def test_estimate_memory_is_instant_even_for_a_billion_parameter_config() -> None:
    # The whole point: no nn.Module gets built, so this must be fast
    # regardless of model size -- proves this really is pure arithmetic.
    xl_config = ModelConfig(
        name="xl_1b",
        vocab_size=50304,
        d_model=2048,
        n_layers=22,
        n_heads=32,
        n_kv_heads=32,
        d_ff=5632,
        context_length=2048,
    )
    start = time.perf_counter()
    estimate = estimate_memory(xl_config, batch_size=1)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
    assert estimate.param_count > 1_000_000_000
    assert estimate.weight_memory_fp32 > 4_000_000_000  # > 4GB at fp32
    assert estimate.total_training_memory > estimate.weight_memory_fp32  # grads + optimizer + activations add up
    assert estimate.summary_lines()  # doesn't crash on a config this size
