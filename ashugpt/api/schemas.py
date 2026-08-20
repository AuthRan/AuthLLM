"""Request/response schemas for the inference API -- pure data contracts,
no model code. FastAPI uses these Pydantic models to validate incoming
JSON automatically (a malformed request never reaches the endpoint
handler; it gets a 422 with a field-level error list instead)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Text to continue")
    max_new_tokens: int = Field(default=100, gt=0, le=2048, description="How many new tokens to generate")
    temperature: float = Field(default=0.8, ge=0.0, description="0.0 = greedy decoding")
    top_k: int | None = Field(default=50, gt=0)
    top_p: float | None = Field(default=0.9, gt=0.0, le=1.0)
    instruct: bool = Field(
        default=False,
        description=(
            "Wrap the prompt in the instruction-tuning template before generating. "
            "Required for checkpoints from scripts/finetune.py, wrong for base ones: "
            "an instruction-tuned model answers inside that template, and a base model "
            "handed it just writes more instructions."
        ),
    )


class GenerateResponse(BaseModel):
    generated_text: str
    tokens_generated: int
    generation_time: float  # seconds
    tokens_per_second: float


class HealthResponse(BaseModel):
    status: str  # "ok" | "loading"
    model_loaded: bool
    checkpoint_path: str | None = None
    parameter_count: int | None = None


class StreamChunkResponse(BaseModel):
    """One server-sent event from POST /generate/stream. `done` marks the
    final event, which carries no text -- it exists so a client knows the
    difference between "the model stopped" and "the connection dropped"."""

    text: str
    tokens_generated: int
    done: bool
