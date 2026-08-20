"""Unit tests for the FastAPI inference server (Milestone 12).

Uses FastAPI's TestClient (in-process, no real socket/uvicorn process
needed) against a tiny untrained checkpoint built as a fixture -- these
tests are about the HTTP layer (validation, status codes, load-once
behavior), not generation quality.
"""

from __future__ import annotations

import json
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


# ---- the browser frontend (SPEC M15) ----


def test_index_serves_the_frontend(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>AshuGPT</title>" in response.text


def test_frontend_makes_no_external_requests() -> None:
    """The page has to work from a checkout with no network. A CDN link for
    a font or a framework would make an offline demo silently degrade (or
    hang), so the absence of one is asserted rather than left to review."""
    page = (Path(__file__).resolve().parents[2] / "ashugpt/api/static/index.html").read_text(encoding="utf-8")
    for marker in ("http://", "https://", "//cdn", "integrity="):
        assert marker not in page, f"frontend references something external: {marker}"


def test_frontend_uses_relative_api_paths() -> None:
    # Absolute "/generate" would break the moment the app is mounted under a
    # path prefix behind a reverse proxy, which is the normal way to deploy it.
    page = (Path(__file__).resolve().parents[2] / "ashugpt/api/static/index.html").read_text(encoding="utf-8")
    assert 'fetch("generate/stream"' in page
    assert 'fetch("health")' in page


# ---- /generate/stream ----


def _sse_events(raw: str) -> list[dict]:
    events = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            events.append(json.loads(block[len("data:") :].strip()))
    return events


def test_stream_sends_events_and_terminates(client: TestClient) -> None:
    response = client.post("/generate/stream", json={"prompt": "the quick", "max_new_tokens": 8})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(response.text)
    assert events, "no SSE events received"
    assert events[-1]["done"] is True
    assert not any(e.get("error") for e in events)
    assert events[-1]["tokens_generated"] > 0


def test_streamed_text_concatenates_to_a_completion(client: TestClient) -> None:
    response = client.post("/generate/stream", json={"prompt": "the quick", "max_new_tokens": 8})
    text = "".join(e.get("text", "") for e in _sse_events(response.text))
    assert isinstance(text, str) and text != ""


def test_stream_rejects_an_overlong_prompt_with_400_not_a_broken_stream(client: TestClient) -> None:
    """The whole reason service.stream() validates eagerly. If the check
    happened at the first token the status line would already say 200, and
    a client would have to discover the failure by parsing an error event
    out of a stream it thought was fine."""
    response = client.post(
        "/generate/stream",
        json={"prompt": "the quick brown fox " * 30, "max_new_tokens": CONTEXT_LENGTH},
    )
    assert response.status_code == 400
    assert "context_length" in response.json()["detail"]


def test_stream_and_generate_agree_given_the_same_seed(client: TestClient) -> None:
    # Two endpoints, one decoding loop -- if that ever stops being true, the
    # streamed text and the collected text diverge for the same seed.
    torch.manual_seed(1234)
    streamed = "".join(
        e.get("text", "")
        for e in _sse_events(
            client.post(
                "/generate/stream",
                json={"prompt": "the quick", "max_new_tokens": 12, "temperature": 0.0},
            ).text
        )
    )
    torch.manual_seed(1234)
    collected = client.post(
        "/generate", json={"prompt": "the quick", "max_new_tokens": 12, "temperature": 0.0}
    ).json()["generated_text"]

    # /generate returns prompt + continuation; the stream returns only what
    # is new, so the stream's text is the tail of the collected text.
    assert collected.endswith(streamed)


# ---- instruct mode ----


def test_instruct_mode_wraps_the_prompt_in_the_template(client: TestClient) -> None:
    from ashugpt.api.service import InferenceService

    with patch.object(InferenceService, "generate", wraps=client.app.state.service.generate) as spy:
        client.post("/generate", json={"prompt": "Explain gravity", "max_new_tokens": 5, "instruct": True})
        assert spy.call_args.kwargs["instruct"] is True


def test_strip_template_returns_only_the_answer() -> None:
    from ashugpt.api.service import _strip_template

    decoded = (
        "Below is an instruction that describes a task. Write a response that "
        "appropriately completes the request.\n\n### Instruction:\nExplain gravity\n\n"
        "### Response:\nMass bends spacetime."
    )
    assert _strip_template(decoded) == "Mass bends spacetime."


def test_instruct_mode_wraps_and_unwraps_end_to_end(checkpoint_and_tokenizer, tmp_path) -> None:
    """Runs against a wider context than the module fixture on purpose: the
    template is ~40 words of boilerplate, which is 150 tokens at this test
    tokenizer's 300-token vocabulary and does not fit in 64. That is a
    property of the fixture, not of the feature -- the real 124M model has
    a 1,024-token context and the template costs it ~35."""
    from ashugpt.api.service import InferenceService

    _, tokenizer_path = checkpoint_and_tokenizer
    tokenizer = BPETokenizer.load(tokenizer_path)
    model_config = ModelConfig(
        name="instruct-test",
        vocab_size=tokenizer.vocab_size,
        d_model=16,
        n_layers=2,
        n_heads=2,
        n_kv_heads=2,
        d_ff=32,
        context_length=512,
    )
    train_config = TrainConfig(batch_size=2, seq_len=8, max_steps=1, warmup_steps=1, max_lr=1e-3, min_lr=1e-4)
    torch.manual_seed(0)
    model = AshuGPT(model_config)
    checkpoint_path = tmp_path / "wide.pt"
    save_checkpoint(
        checkpoint_path,
        model,
        build_optimizer(model, lr=1e-3, weight_decay=0.0),
        step=0,
        model_config=model_config,
        train_config=train_config,
    )

    service = InferenceService.load(checkpoint_path, tokenizer_path, device="cpu")
    result = service.generate(
        prompt="Explain gravity", max_new_tokens=8, temperature=0.0, top_k=None, top_p=None, instruct=True
    )
    # The user asked a question; they should not get the boilerplate back.
    assert "### Instruction:" not in result.generated_text
    assert "Below is an instruction" not in result.generated_text

    # And the stream of the same request carries only new text, never the prompt.
    streamed = "".join(
        chunk.text
        for chunk in service.stream(
            prompt="Explain gravity", max_new_tokens=8, temperature=0.0, top_k=None, top_p=None, instruct=True
        )
    )
    assert "### Instruction:" not in streamed
