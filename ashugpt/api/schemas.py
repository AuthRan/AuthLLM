"""Request/response schemas for the inference API -- pure data contracts,
no model code. FastAPI uses these Pydantic models to validate incoming
JSON automatically (a malformed request never reaches the endpoint
handler; it gets a 422 with a field-level error list instead)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """One prior turn of a conversation.

    Only `user` and `assistant` are accepted from a client: a `system` turn
    is legal in the training format but must come first, and letting a
    client interleave one anywhere is the quickest way to send the model a
    document shape it was never trained on.
    """

    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1)


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
    chat: bool = Field(
        default=False,
        description=(
            "Wrap the prompt and `history` in the multi-turn chat template "
            "(README section 10.7) before generating. Required for a checkpoint "
            "trained with scripts/finetune.py --format chat. Mutually exclusive "
            "with `instruct`: the two are different document formats, and a "
            "request asking for both is a bug rather than a preference."
        ),
    )
    history: list[ChatMessage] = Field(
        default_factory=list,
        description=(
            "Turns already exchanged, oldest first, excluding the current `prompt`. "
            "Ignored unless `chat` is true. The server is stateless -- a client that "
            "wants the model to remember the conversation sends it back every time."
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
    prompt_format: str | None = None
    """Which document format this checkpoint was fine-tuned on.

    "base" | "instruct" | "chat". A checkpoint file does not record what it
    was trained on, and nothing about the weights reveals it, so this is what
    the operator declared at startup. The frontend defaults its mode to it:
    prompting an instruction-tuned checkpoint as raw continuation is the
    single easiest way to make a working model look broken.
    """


class StreamChunkResponse(BaseModel):
    """One server-sent event from POST /generate/stream. `done` marks the
    final event, which carries no text -- it exists so a client knows the
    difference between "the model stopped" and "the connection dropped"."""

    text: str
    tokens_generated: int
    done: bool
