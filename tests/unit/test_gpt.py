"""Unit tests for the full AshuGPT model."""

from pathlib import Path

import pytest
import torch
import torch.nn.functional as F

from ashugpt.config import ModelConfig, load_model_config
from ashugpt.model.gpt import AshuGPT

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "model"


def tiny_test_config(vocab_size: int = 64, tie_embeddings: bool = True) -> ModelConfig:
    return ModelConfig(
        name="unit-test",
        vocab_size=vocab_size,
        d_model=32,
        n_layers=2,
        n_heads=4,
        n_kv_heads=4,
        d_ff=64,
        context_length=32,
        tie_embeddings=tie_embeddings,
    )


@pytest.fixture
def model() -> AshuGPT:
    torch.manual_seed(0)
    return AshuGPT(tiny_test_config())


# ---- forward pass / output dimensions ----


def test_forward_pass_runs(model: AshuGPT) -> None:
    input_ids = torch.randint(0, model.config.vocab_size, (2, 10))
    out = model(input_ids)
    assert out.logits is not None
    assert out.loss is None  # no labels given


def test_output_dimensions(model: AshuGPT) -> None:
    batch, seq_len = 3, 7
    input_ids = torch.randint(0, model.config.vocab_size, (batch, seq_len))
    out = model(input_ids)
    assert out.logits.shape == (batch, seq_len, model.config.vocab_size)
    assert out.logits.dtype == torch.float32


@pytest.mark.parametrize("batch_size", [1, 2, 5, 8])
def test_different_batch_sizes(model: AshuGPT, batch_size: int) -> None:
    seq_len = 6
    input_ids = torch.randint(0, model.config.vocab_size, (batch_size, seq_len))
    out = model(input_ids)
    assert out.logits.shape == (batch_size, seq_len, model.config.vocab_size)


@pytest.mark.parametrize("seq_len", [1, 4, 16, 32])
def test_different_sequence_lengths(model: AshuGPT, seq_len: int) -> None:
    input_ids = torch.randint(0, model.config.vocab_size, (2, seq_len))
    out = model(input_ids)
    assert out.logits.shape == (2, seq_len, model.config.vocab_size)


def test_sequence_length_beyond_context_length_raises(model: AshuGPT) -> None:
    too_long = model.config.context_length + 1
    input_ids = torch.randint(0, model.config.vocab_size, (1, too_long))
    with pytest.raises(ValueError):
        model(input_ids)


# ---- loss calculation ----


def test_loss_is_none_without_labels(model: AshuGPT) -> None:
    input_ids = torch.randint(0, model.config.vocab_size, (2, 8))
    assert model(input_ids).loss is None


def test_loss_matches_manual_cross_entropy(model: AshuGPT) -> None:
    input_ids = torch.randint(0, model.config.vocab_size, (2, 8))
    labels = torch.randint(0, model.config.vocab_size, (2, 8))
    out = model(input_ids, labels=labels)

    expected = F.cross_entropy(out.logits.view(-1, model.config.vocab_size), labels.view(-1))
    assert torch.allclose(out.loss, expected)
    assert out.loss.ndim == 0  # scalar
    assert torch.isfinite(out.loss)


def test_loss_reflects_the_one_token_shift() -> None:
    # Mirrors the "The cat sat" -> "cat sat down" example directly: labels
    # is a *different*, already-shifted id sequence, not input_ids restated.
    torch.manual_seed(0)
    config = tiny_test_config(vocab_size=20)
    model = AshuGPT(config)

    input_ids = torch.tensor([[1, 2, 3]])  # "The cat sat"
    labels = torch.tensor([[2, 3, 4]])  # "cat sat down"
    out = model(input_ids, labels=labels)

    assert out.loss is not None
    assert torch.isfinite(out.loss)
    # Position t's loss term only depends on logits[:, t, :] vs labels[:, t];
    # confirm that's exactly what cross_entropy over the flattened tensors computes.
    per_position = torch.stack(
        [F.cross_entropy(out.logits[0, t], labels[0, t]) for t in range(input_ids.shape[1])]
    )
    assert torch.allclose(out.loss, per_position.mean(), atol=1e-5)


def test_loss_respects_ignore_index_minus_100() -> None:
    torch.manual_seed(0)
    model = AshuGPT(tiny_test_config())
    input_ids = torch.randint(0, model.config.vocab_size, (1, 6))
    labels = torch.randint(0, model.config.vocab_size, (1, 6))
    labels_partially_masked = labels.clone()
    labels_partially_masked[:, -2:] = -100  # simulate padding positions

    loss_unmasked = model(input_ids, labels=labels).loss
    loss_masked = model(input_ids, labels=labels_partially_masked).loss
    assert torch.isfinite(loss_masked)
    assert not torch.allclose(loss_unmasked, loss_masked)


# ---- backpropagation ----


def test_backpropagation_reaches_every_parameter(model: AshuGPT) -> None:
    input_ids = torch.randint(0, model.config.vocab_size, (2, 8))
    labels = torch.randint(0, model.config.vocab_size, (2, 8))
    out = model(input_ids, labels=labels)
    out.loss.backward()

    for name, param in model.named_parameters():
        assert param.grad is not None, f"{name} received no gradient"
        assert torch.isfinite(param.grad).all(), f"{name} got a non-finite gradient"


def test_backpropagation_without_labels(model: AshuGPT) -> None:
    input_ids = torch.randint(0, model.config.vocab_size, (2, 8))
    out = model(input_ids)
    out.logits.sum().backward()
    for name, param in model.named_parameters():
        assert param.grad is not None, f"{name} received no gradient"


# ---- parameter counting ----


def test_num_parameters_matches_approx_param_count_tied() -> None:
    config = tiny_test_config(tie_embeddings=True)
    torch.manual_seed(0)
    model = AshuGPT(config)
    assert model.num_parameters() == config.approx_param_count()


def test_num_parameters_matches_approx_param_count_untied() -> None:
    config = tiny_test_config(tie_embeddings=False)
    torch.manual_seed(0)
    model = AshuGPT(config)
    assert model.num_parameters() == config.approx_param_count()


def test_tied_embeddings_share_the_same_tensor() -> None:
    tied = AshuGPT(tiny_test_config(tie_embeddings=True))
    assert tied.lm_head.weight is tied.token_embedding.weight

    untied = AshuGPT(tiny_test_config(tie_embeddings=False))
    assert untied.lm_head.weight is not untied.token_embedding.weight


def test_num_parameters_excluding_embeddings_is_smaller(model: AshuGPT) -> None:
    total = model.num_parameters()
    non_embedding = model.num_parameters(exclude_embeddings=True)
    assert non_embedding < total
    assert non_embedding == total - model.token_embedding.weight.numel()


@pytest.mark.parametrize("preset", ["tiny", "small"])
def test_matches_real_preset_configs_exactly(preset: str) -> None:
    # Ties this milestone back to Milestone 1's presets: the shape-only
    # estimate and the actual constructed model must agree exactly.
    config = load_model_config(CONFIGS_DIR / f"{preset}.yaml")
    torch.manual_seed(0)
    model = AshuGPT(config)
    assert model.num_parameters() == config.approx_param_count()


def test_residual_projections_get_scaled_init() -> None:
    torch.manual_seed(0)
    model = AshuGPT(tiny_test_config())
    q_proj_std = model.blocks[0].attn.q_proj.weight.std().item()
    o_proj_std = model.blocks[0].attn.o_proj.weight.std().item()
    assert o_proj_std < q_proj_std  # residual-stream projection is scaled down


# ---- KV cache: incremental (cached) forward vs. one full forward pass ----


def test_kv_cache_incremental_forward_matches_full_forward(model: AshuGPT) -> None:
    # Full-model-level version of the equivalence already proven at the
    # attention/block level in Milestone 3-4: processing a sequence one
    # (or a few) tokens at a time via kv_caches/position_offset must give
    # the same logits as processing the whole thing in a single call.
    model.eval()
    torch.manual_seed(1)
    full_sequence = torch.randint(0, model.config.vocab_size, (2, 12))

    with torch.no_grad():
        full_output = model(full_sequence, kv_caches=None, position_offset=0)

        # Feed the prompt (first 5 tokens) in one call, then the rest one token at a time.
        prompt, rest = full_sequence[:, :5], full_sequence[:, 5:]
        output = model(prompt, kv_caches=None, position_offset=0)
        kv_caches = output.kv_caches
        incremental_logits = [output.logits]

        for t in range(rest.shape[1]):
            token = rest[:, t : t + 1]
            output = model(token, kv_caches=kv_caches, position_offset=5 + t)
            kv_caches = output.kv_caches
            incremental_logits.append(output.logits)

    incremental_logits = torch.cat(incremental_logits, dim=1)  # (batch, 12, vocab_size)
    assert torch.allclose(incremental_logits, full_output.logits, atol=1e-4)


# ---- Memory optimizations (Milestone 9) ----


def test_set_memory_optimizations_toggles_independently(model: AshuGPT) -> None:
    assert model.gradient_checkpointing is False
    assert all(not b.attn.use_efficient_attention for b in model.blocks)

    model.set_memory_optimizations(gradient_checkpointing=True)
    assert model.gradient_checkpointing is True
    assert all(not b.attn.use_efficient_attention for b in model.blocks)  # untouched by this call

    model.set_memory_optimizations(efficient_attention=True)
    assert model.gradient_checkpointing is True  # untouched by this call
    assert all(b.attn.use_efficient_attention for b in model.blocks)

    model.set_memory_optimizations(gradient_checkpointing=False, efficient_attention=False)
    assert model.gradient_checkpointing is False
    assert all(not b.attn.use_efficient_attention for b in model.blocks)


def test_gradient_checkpointing_matches_normal_forward_and_gradients() -> None:
    config = tiny_test_config()
    torch.manual_seed(0)
    model_normal = AshuGPT(config)
    torch.manual_seed(0)
    model_checkpointed = AshuGPT(config)
    model_checkpointed.set_memory_optimizations(gradient_checkpointing=True)

    model_normal.train()
    model_checkpointed.train()
    input_ids = torch.randint(0, config.vocab_size, (2, 16))
    labels = torch.randint(0, config.vocab_size, (2, 16))

    out_normal = model_normal(input_ids, labels=labels)
    out_checkpointed = model_checkpointed(input_ids, labels=labels)
    assert torch.allclose(out_normal.logits, out_checkpointed.logits, atol=1e-5)
    assert torch.allclose(out_normal.loss, out_checkpointed.loss, atol=1e-5)

    out_normal.loss.backward()
    out_checkpointed.loss.backward()
    for (name, p_normal), (_, p_checkpointed) in zip(
        model_normal.named_parameters(), model_checkpointed.named_parameters()
    ):
        assert p_checkpointed.grad is not None, f"{name} got no gradient under checkpointing"
        assert torch.allclose(p_normal.grad, p_checkpointed.grad, atol=1e-4), f"{name} gradient mismatch"


def test_gradient_checkpointing_is_inactive_in_eval_mode(model: AshuGPT) -> None:
    # No backward pass in eval mode, so there's nothing for checkpointing
    # to save memory on -- confirms the flag alone doesn't force it on
    # outside of training, where it would only add recompute overhead for
    # no benefit.
    model.set_memory_optimizations(gradient_checkpointing=True)
    model.eval()
    input_ids = torch.randint(0, model.config.vocab_size, (2, 8))
    with torch.no_grad():
        out = model(input_ids)  # must not raise (e.g. checkpoint complaining about no grad context)
    assert out.logits.shape == (2, 8, model.config.vocab_size)


def test_gradient_checkpointing_disabled_when_using_kv_cache() -> None:
    # Checkpointing recomputes a block's forward from scratch during
    # backward, which can't be reconciled with a growing KV cache -- but
    # generation (the only kv_caches user) never has a backward pass
    # anyway, so this is a defense-in-depth check, not a realistic path.
    config = tiny_test_config()
    torch.manual_seed(0)
    model = AshuGPT(config)
    model.set_memory_optimizations(gradient_checkpointing=True)
    model.train()  # force training=True to isolate the kv_caches-is-not-None guard specifically

    input_ids = torch.randint(0, config.vocab_size, (1, 4))
    output = model(input_ids, kv_caches=None, position_offset=0)
    # Must not raise when continuing from a cache even with the flag on.
    next_token = torch.randint(0, config.vocab_size, (1, 1))
    model(next_token, kv_caches=output.kv_caches, position_offset=4)
