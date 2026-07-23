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

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException

from ashugpt.api.schemas import GenerateRequest, GenerateResponse, HealthResponse
from ashugpt.api.service import InferenceService

CHECKPOINT_ENV_VAR = "ASHUGPT_CHECKPOINT"
TOKENIZER_ENV_VAR = "ASHUGPT_TOKENIZER"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    checkpoint_path = os.environ.get(CHECKPOINT_ENV_VAR)
    tokenizer_path = os.environ.get(TOKENIZER_ENV_VAR)
    if not checkpoint_path or not tokenizer_path:
        raise RuntimeError(
            f"Set {CHECKPOINT_ENV_VAR} and {TOKENIZER_ENV_VAR} before starting the server "
            f"(scripts/serve.py does this for you from --checkpoint/--tokenizer flags)."
        )
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
