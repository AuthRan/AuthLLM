"""FastAPI HTTP layer for AshuGPT inference.

Imports only ashugpt.api.service.InferenceService -- never
ashugpt.model/ashugpt.tokenizer/ashugpt.inference directly. This file
knows about HTTP status codes, request/response JSON, and startup
config; it does not know how a transformer forward pass works, and
never will need to change when one does.

Model loading happens once, in the lifespan handler below, at process
startup -- not inside the /generate handler, which only ever calls
into the already-loaded InferenceService.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from ashugpt.api.schemas import GenerateRequest, GenerateResponse, HealthResponse
from ashugpt.api.service import InferenceService

CHECKPOINT_ENV_VAR = "ASHUGPT_CHECKPOINT"
TOKENIZER_ENV_VAR = "ASHUGPT_TOKENIZER"
FORMAT_ENV_VAR = "ASHUGPT_FORMAT"

# What a checkpoint was fine-tuned on. Nothing in the weights says which, so
# the operator declares it and the frontend follows. "base" is the default
# because it is the only one that is never actively wrong: a base checkpoint
# continues text, and continuation is what an undeclared checkpoint gets.
PROMPT_FORMATS = ("base", "instruct", "chat")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    checkpoint_path = os.environ.get(CHECKPOINT_ENV_VAR)
    tokenizer_path = os.environ.get(TOKENIZER_ENV_VAR)
    if not checkpoint_path or not tokenizer_path:
        raise RuntimeError(
            f"Set {CHECKPOINT_ENV_VAR} and {TOKENIZER_ENV_VAR} before starting the server "
            f"(scripts/serve.py does this for you from --checkpoint/--tokenizer flags)."
        )
    prompt_format = os.environ.get(FORMAT_ENV_VAR, "base")
    if prompt_format not in PROMPT_FORMATS:
        raise RuntimeError(
            f"{FORMAT_ENV_VAR}={prompt_format!r} is not one of {PROMPT_FORMATS}."
        )
    app.state.prompt_format = prompt_format
    app.state.service = InferenceService.load(checkpoint_path, tokenizer_path)
    yield
    # Nothing to release: no open connections/file handles outlive the process.


app = FastAPI(
    title="AshuGPT Inference API",
    description="Educational text-generation API serving a self-trained AshuGPT checkpoint.",
    lifespan=lifespan,
)


def _get_service(app: FastAPI) -> InferenceService:
    service = getattr(app.state, "service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Model is not loaded yet")
    return service


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    service = getattr(app.state, "service", None)
    if service is None:
        return HealthResponse(status="loading", model_loaded=False)
    return HealthResponse(
        status="ok",
        model_loaded=True,
        checkpoint_path=service.checkpoint_path,
        parameter_count=service.parameter_count,
        prompt_format=getattr(app.state, "prompt_format", "base"),
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """The browser frontend (SPEC.md M15's other half).

    A single self-contained file with no build step, no framework, and no
    external requests: the page has to work from a checkout, offline, on a
    machine that has torch and nothing else. It is served from a route
    rather than a StaticFiles mount so that mounting does not shadow the
    API routes above it.
    """
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.post("/generate/stream", include_in_schema=True)
def generate_stream_endpoint(request: GenerateRequest) -> StreamingResponse:
    """Server-sent events, one per decoded chunk of text.

    SSE rather than a WebSocket because the traffic is entirely one-way --
    the client sends a prompt and then only listens -- and SSE is a plain
    HTTP response that needs no protocol upgrade, no ping/pong keepalive,
    and no second code path on the server for connection state.

    Sync `def` for the same reason as /generate below: torch decoding
    blocks, so Starlette runs this in a worker thread and iterates the
    generator from there, leaving the event loop free.

    Validation errors have to be raised *before* the StreamingResponse is
    constructed. Once the response object exists the status line is
    committed, and a failure after that point can only be reported as an
    error event inside a 200 -- which is why service.stream() does its
    context-length check eagerly rather than at the first token.
    """
    service = _get_service(app)

    try:
        chunks = service.stream(
            prompt=request.prompt,
            max_new_tokens=request.max_new_tokens,
            temperature=request.temperature,
            top_k=request.top_k,
            top_p=request.top_p,
            repetition_penalty=request.repetition_penalty,
            instruct=request.instruct,
            chat=request.chat,
            history=[(m.role, m.content) for m in request.history],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    def event_stream() -> Iterator[str]:
        try:
            for chunk in chunks:
                payload = {"text": chunk.text, "tokens_generated": chunk.tokens_generated, "done": chunk.done}
                yield f"data: {json.dumps(payload)}\n\n"
        except Exception as e:  # noqa: BLE001 -- status line already sent; the only place left to report is in-band
            yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/generate", response_model=GenerateResponse)
def generate_endpoint(request: GenerateRequest) -> GenerateResponse:
    """Runs synchronously (plain `def`, not `async def`) on purpose:
    torch inference on CPU is blocking, CPU-bound work. FastAPI/Starlette
    dispatch sync handlers to a worker thread pool automatically, which
    keeps the event loop free; an `async def` handler doing the same
    blocking work would instead freeze the whole server for every other
    request until this one finished."""
    service = _get_service(app)
    try:
        result = service.generate(
            prompt=request.prompt,
            max_new_tokens=request.max_new_tokens,
            temperature=request.temperature,
            top_k=request.top_k,
            top_p=request.top_p,
            repetition_penalty=request.repetition_penalty,
            instruct=request.instruct,
            chat=request.chat,
            history=[(m.role, m.content) for m in request.history],
        )
    except ValueError as e:
        # A request the model itself rejects (e.g. prompt + max_new_tokens
        # exceeds context_length) -- a client error, not a server failure.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 -- deliberately broad: never leak a raw traceback to a client
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}") from e

    return GenerateResponse(
        generated_text=result.generated_text,
        tokens_generated=result.tokens_generated,
        generation_time=result.generation_time,
        tokens_per_second=result.tokens_per_second,
    )
