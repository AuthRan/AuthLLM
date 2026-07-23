"""Unit tests for GPT-2 -> AshuGPT checkpoint loading (Milestone 11).

Uses synthetic tensors at a small scale, built to the *exact* key-naming
and shape conventions verified against the real openai-community/gpt2
checkpoint's safetensors header and config.json (see
pretrained_loader.py's module docstring) -- not against a full 548MB
download, since the architectural-compatibility question this milestone
is actually about is fully answered by names/shapes, not tensor values.
"""

from __future__ import annotations

import pytest
import torch

from ashugpt.config import ModelConfig
from ashugpt.inference.pretrained_loader import (
    GPT2_FUNDAMENTAL_INCOMPATIBILITIES,
    CompatibilityReport,
    IncompatibleArchitectureError,
    convert_gpt2_state_dict,
    gpt2_config_to_model_config,
    load_gpt2_checkpoint,
)

# A small GPT-2-shaped config -- same field names/relationships as the
# real one (d_ff = 4*n_embd, tied embeddings), just tiny for fast tests.
SMALL_GPT2_CONFIG = {"vocab_size": 37, "n_positions": 16, "n_embd": 8, "n_layer": 2, "n_head": 2}


def build_synthetic_gpt2_state_dict(gpt2_config: dict) -> dict[str, torch.Tensor]:
    """A state dict with the real GPT-2 key names and shapes (verified
    live against the real checkpoint's safetensors header), filled with
    distinguishable (not random) values so tests can check the conversion
    moved the *right* numbers, not just tensors of the right shape."""
    d_model = gpt2_config["n_embd"]
    n_layer = gpt2_config["n_layer"]
    vocab_size = gpt2_config["vocab_size"]
    n_positions = gpt2_config["n_positions"]
    d_ff = 4 * d_model

    state = {
        "wte.weight": torch.arange(vocab_size * d_model, dtype=torch.float32).reshape(vocab_size, d_model),
        "wpe.weight": torch.arange(n_positions * d_model, dtype=torch.float32).reshape(n_positions, d_model),
        "ln_f.weight": torch.full((d_model,), 9.0),
        "ln_f.bias": torch.full((d_model,), -9.0),
    }
    for i in range(n_layer):
        p = f"h.{i}."
        # c_attn: (d_model, 3*d_model) in Conv1D (in, out) layout. Fill each
        # third of the *output* dimension with a distinct constant so the
        # Q/K/V split is independently checkable.
        c_attn = torch.cat(
            [
                torch.full((d_model, d_model), float(10 + i)),  # -> Q
                torch.full((d_model, d_model), float(20 + i)),  # -> K
                torch.full((d_model, d_model), float(30 + i)),  # -> V
            ],
            dim=1,
        )
        state[p + "ln_1.weight"] = torch.full((d_model,), float(1 + i))
        state[p + "ln_1.bias"] = torch.full((d_model,), float(-1 - i))
        state[p + "attn.c_attn.weight"] = c_attn
        state[p + "attn.c_attn.bias"] = torch.full((3 * d_model,), float(40 + i))
        state[p + "attn.c_proj.weight"] = torch.full((d_model, d_model), float(50 + i))
        state[p + "attn.c_proj.bias"] = torch.full((d_model,), float(60 + i))
        state[p + "attn.bias"] = torch.tril(torch.ones(n_positions, n_positions)).view(1, 1, n_positions, n_positions)
        state[p + "ln_2.weight"] = torch.full((d_model,), float(2 + i))
        state[p + "ln_2.bias"] = torch.full((d_model,), float(-2 - i))
        state[p + "mlp.c_fc.weight"] = torch.full((d_model, d_ff), float(70 + i))
        state[p + "mlp.c_fc.bias"] = torch.full((d_ff,), float(80 + i))
        state[p + "mlp.c_proj.weight"] = torch.full((d_ff, d_model), float(90 + i))
        state[p + "mlp.c_proj.bias"] = torch.full((d_model,), float(100 + i))
    return state


# ---- config translation ----


def test_gpt2_config_to_model_config_matches_real_verified_values() -> None:
    real_gpt2_config = {"vocab_size": 50257, "n_positions": 1024, "n_embd": 768, "n_layer": 12, "n_head": 12}
    config = gpt2_config_to_model_config(real_gpt2_config)
    assert config.vocab_size == 50257
    assert config.d_model == 768
    assert config.n_layers == 12
    assert config.n_heads == 12
    assert config.n_kv_heads == 12
    assert config.d_ff == 3072  # 4 * 768, matches the real mlp.c_fc shape (768, 3072)
    assert config.context_length == 1024
    assert config.tie_embeddings is True


# ---- conversion correctness ----


def test_convert_splits_c_attn_into_correct_q_k_v() -> None:
    source = build_synthetic_gpt2_state_dict(SMALL_GPT2_CONFIG)
    config = gpt2_config_to_model_config(SMALL_GPT2_CONFIG)
    converted, _ = convert_gpt2_state_dict(source, config)

    d_model = SMALL_GPT2_CONFIG["n_embd"]
    # Layer 0's c_attn was built as [Q=10, K=20, V=30] constants.
    assert torch.equal(converted["blocks.0.attn.q_proj.weight"], torch.full((d_model, d_model), 10.0))
    assert torch.equal(converted["blocks.0.attn.k_proj.weight"], torch.full((d_model, d_model), 20.0))
    assert torch.equal(converted["blocks.0.attn.v_proj.weight"], torch.full((d_model, d_model), 30.0))


def test_convert_transposes_conv1d_weights_to_nn_linear_layout() -> None:
    # Use a non-constant, non-square-symmetric matrix so a missed
    # transpose would actually change the numbers, not go unnoticed.
    config = ModelConfig(name="t", vocab_size=10, d_model=3, n_layers=1, n_heads=1, n_kv_heads=1, d_ff=12, context_length=8)
    c_proj = torch.tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]])
    source = {"h.0.attn.c_proj.weight": c_proj}
    converted, _ = convert_gpt2_state_dict(source, config)
    assert torch.equal(converted["blocks.0.attn.o_proj.weight"], c_proj.t())


def test_convert_maps_embeddings_and_norms() -> None:
    source = build_synthetic_gpt2_state_dict(SMALL_GPT2_CONFIG)
    config = gpt2_config_to_model_config(SMALL_GPT2_CONFIG)
    converted, _ = convert_gpt2_state_dict(source, config)

    assert torch.equal(converted["token_embedding.weight"], source["wte.weight"])
    assert torch.equal(converted["lm_head.weight"], source["wte.weight"])  # tied
    assert torch.equal(converted["final_norm.weight"], source["ln_f.weight"])
    assert torch.equal(converted["blocks.0.attn_norm.weight"], source["h.0.ln_1.weight"])
    assert torch.equal(converted["blocks.0.ffn_norm.weight"], source["h.0.ln_2.weight"])


def test_convert_reports_expected_unexpected_keys() -> None:
    source = build_synthetic_gpt2_state_dict(SMALL_GPT2_CONFIG)
    config = gpt2_config_to_model_config(SMALL_GPT2_CONFIG)
    _, report = convert_gpt2_state_dict(source, config)

    # Positional embeddings, every bias, every FFN weight, and the static
    # causal-mask buffer all have no destination -- must be reported, not
    # silently dropped.
    assert "wpe.weight" in report.unexpected_keys
    assert "ln_f.bias" in report.unexpected_keys
    assert "h.0.attn.c_attn.bias" in report.unexpected_keys
    assert "h.0.mlp.c_fc.weight" in report.unexpected_keys
    assert "h.0.mlp.c_proj.weight" in report.unexpected_keys
    assert "h.0.attn.bias" in report.unexpected_keys  # the static mask buffer, not a real weight

    # Things that WERE consumed must not also show up as unexpected.
    assert "wte.weight" not in report.unexpected_keys
    assert "h.0.attn.c_attn.weight" not in report.unexpected_keys


def test_convert_always_reports_fundamental_incompatibilities() -> None:
    source = build_synthetic_gpt2_state_dict(SMALL_GPT2_CONFIG)
    config = gpt2_config_to_model_config(SMALL_GPT2_CONFIG)
    _, report = convert_gpt2_state_dict(source, config)
    assert report.fundamental_incompatibilities == GPT2_FUNDAMENTAL_INCOMPATIBILITIES
    assert len(report.fundamental_incompatibilities) >= 3  # positional encoding, FFN gating, normalization
    assert not report.is_fully_compatible


# ---- load_gpt2_checkpoint: fail loudly by default ----


def test_load_gpt2_checkpoint_raises_by_default() -> None:
    source = build_synthetic_gpt2_state_dict(SMALL_GPT2_CONFIG)
    with pytest.raises(IncompatibleArchitectureError) as exc_info:
        load_gpt2_checkpoint(source, gpt2_config=SMALL_GPT2_CONFIG)

    message = str(exc_info.value)
    assert "FUNDAMENTAL ARCHITECTURE INCOMPATIBILITIES" in message
    assert "Positional encoding" in message
    assert "Feed-forward" in message


def test_load_gpt2_checkpoint_strict_false_returns_partial_model_and_report() -> None:
    source = build_synthetic_gpt2_state_dict(SMALL_GPT2_CONFIG)
    model, report = load_gpt2_checkpoint(source, gpt2_config=SMALL_GPT2_CONFIG, strict=False)

    assert not report.is_fully_compatible
    assert report.fundamental_incompatibilities  # still populated, even though we didn't raise

    # Missing keys are computed from the REAL model's actual parameter
    # names (via model.load_state_dict), not guessed -- every SwiGLU
    # weight should show up, since nothing in a GPT-2 checkpoint maps to it.
    assert any("gate_proj" in k for k in report.missing_keys)
    assert any("up_proj" in k for k in report.missing_keys)
    assert any("down_proj" in k for k in report.missing_keys)


def test_load_gpt2_checkpoint_actually_loads_the_mappable_weights_correctly() -> None:
    # Not just "doesn't crash" -- the partially-loaded model's attention
    # weights must contain the exact values the synthetic checkpoint
    # specified, proving the conversion is numerically correct end to end.
    source = build_synthetic_gpt2_state_dict(SMALL_GPT2_CONFIG)
    model, _ = load_gpt2_checkpoint(source, gpt2_config=SMALL_GPT2_CONFIG, strict=False)

    d_model = SMALL_GPT2_CONFIG["n_embd"]
    assert torch.equal(model.blocks[0].attn.q_proj.weight, torch.full((d_model, d_model), 10.0))
    assert torch.equal(model.blocks[0].attn.k_proj.weight, torch.full((d_model, d_model), 20.0))
    assert torch.equal(model.blocks[0].attn.v_proj.weight, torch.full((d_model, d_model), 30.0))
    assert torch.equal(model.token_embedding.weight, source["wte.weight"])


def test_compatibility_report_summary_mentions_counts() -> None:
    report = CompatibilityReport(
        missing_keys=["a", "b"], unexpected_keys=["c"], fundamental_incompatibilities=["reason 1"]
    )
    text = report.summary()
    assert "Missing keys (2)" in text
    assert "Unexpected keys (1)" in text
    assert "reason 1" in text
