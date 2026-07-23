"""Unit tests for the FastAPI inference server (Milestone 12).

Uses FastAPI's TestClient (in-process, no real socket/uvicorn process
needed) against a tiny untrained checkpoint built as a fixture -- these
tests are about the HTTP layer (validation, status codes, load-once
behavior), not generation quality.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import torch
from fastapi.testclient import TestClient

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.model.gpt import AshuGPT
from ashugpt.tokenizer import BPETokenizer
from ashugpt.training.checkpoint import save_checkpoint
from ashugpt.training.optim import build_optimizer

CONTEXT_LENGTH = 64


@pytest.fixture(scope="module")
def checkpoint_and_tokenizer(tmp_path_factory) -> tuple[Path, Path]:
    tmp_path = tmp_path_factory.mktemp("api_fixture")

    corpus = "the quick fox jumps over the lazy dog. " * 20
    tokenizer = BPETokenizer.train(corpus, vocab_size=300)
    tokenizer_path = tmp_path / "tokenizer.json"
    tokenizer.save(tokenizer_path)

    model_config = ModelConfig(
        name="api-test",
        vocab_size=tokenizer.vocab_size,
        d_model=16,
        n_layers=2,
        n_heads=2,
        n_kv_heads=2,
        d_ff=32,
        context_length=CONTEXT_LENGTH,
    )
    train_config = TrainConfig(batch_size=2, seq_len=8, max_steps=1, warmup_steps=1, max_lr=1e-3, min_lr=1e-4)

    torch.manual_seed(0)
    model = AshuGPT(model_config)
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.0)
    checkpoint_path = tmp_path / "model.pt"
    save_checkpoint(checkpoint_path, model, optimizer, step=0, model_config=model_config, train_config=train_config)

    return checkpoint_path, tokenizer_path


@pytest.fixture
def client(checkpoint_and_tokenizer: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    checkpoint_path, tokenizer_path = checkpoint_and_tokenizer
    monkeypatch.setenv("ASHUGPT_CHECKPOINT", str(checkpoint_path))
    monkeypatch.setenv("ASHUGPT_TOKENIZER", str(tokenizer_path))

    from ashugpt.api.app import app  # imported here so env vars are set before the module's lifespan runs

    with TestClient(app) as test_client:
        yield test_client


# ---- health ----


def test_health_reports_model_loaded(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["parameter_count"] > 0


# ---- /generate: happy path ----


def test_generate_returns_expected_schema(client: TestClient) -> None:
    response = client.post("/generate", json={"prompt": "the quick", "max_new_tokens": 10})
    assert response.status_code == 200
    body = response.json()

    assert isinstance(body["generated_text"], str)
    assert body["generated_text"] != ""
    assert body["tokens_generated"] > 0
    assert body["generation_time"] > 0
    assert body["tokens_per_second"] > 0


def test_generate_respects_sampling_parameters(client: TestClient) -> None:
    response = client.post(
        "/generate",
        json={"prompt": "the quick", "max_new_tokens": 5, "temperature": 0.5, "top_k": 20, "top_p": 0.95},
    )
    assert response.status_code == 200


def test_generate_greedy_is_deterministic(client: TestClient) -> None:
    body = {"prompt": "the quick", "max_new_tokens": 8, "temperature": 0.0}
    first = client.post("/generate", json=body).json()["generated_text"]
    second = client.post("/generate", json=body).json()["generated_text"]
    assert first == second


def test_tokens_per_second_is_internally_consistent(client: TestClient) -> None:
    body = client.post("/generate", json={"prompt": "the quick", "max_new_tokens": 10}).json()
    expected = body["tokens_generated"] / body["generation_time"]
    assert body["tokens_per_second"] == pytest.approx(expected, rel=1e-6)


# ---- request validation (422) ----


def test_generate_rejects_empty_prompt(client: TestClient) -> None:
    response = client.post("/generate", json={"prompt": ""})
    assert response.status_code == 422


def test_generate_rejects_negative_max_new_tokens(client: TestClient) -> None:
    response = client.post("/generate", json={"prompt": "hi", "max_new_tokens": -5})
    assert response.status_code == 422


def test_generate_rejects_negative_temperature(client: TestClient) -> None:
    response = client.post("/generate", json={"prompt": "hi", "temperature": -0.1})
    assert response.status_code == 422


def test_generate_rejects_out_of_range_top_p(client: TestClient) -> None:
    response = client.post("/generate", json={"prompt": "hi", "top_p": 1.5})
    assert response.status_code == 422


def test_generate_rejects_missing_prompt(client: TestClient) -> None:
    response = client.post("/generate", json={"max_new_tokens": 10})
    assert response.status_code == 422


# ---- model-level rejection (400, not 422 -- valid JSON shape, invalid given the loaded model) ----


def test_generate_beyond_context_length_returns_400(client: TestClient) -> None:
    response = client.post("/generate", json={"prompt": "the quick", "max_new_tokens": CONTEXT_LENGTH + 100})
    assert response.status_code == 400
    assert "context_length" in response.json()["detail"]


# ---- load-once behavior ----


def test_model_is_loaded_exactly_once_across_multiple_requests(
    checkpoint_and_tokenizer: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint_path, tokenizer_path = checkpoint_and_tokenizer
    monkeypatch.setenv("ASHUGPT_CHECKPOINT", str(checkpoint_path))
    monkeypatch.setenv("ASHUGPT_TOKENIZER", str(tokenizer_path))

    from ashugpt.api.app import app
    from ashugpt.api.service import InferenceService

    with patch.object(InferenceService, "load", wraps=InferenceService.load) as mock_load:
        with TestClient(app) as test_client:
            test_client.post("/generate", json={"prompt": "the quick", "max_new_tokens": 5})
            test_client.post("/generate", json={"prompt": "the lazy", "max_new_tokens": 5})
            test_client.get("/health")
        assert mock_load.call_count == 1


def test_missing_env_vars_fail_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASHUGPT_CHECKPOINT", raising=False)
    monkeypatch.delenv("ASHUGPT_TOKENIZER", raising=False)

    from ashugpt.api.app import app

    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
