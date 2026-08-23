"""Gradio chat demo for AshuGPT -- a from-scratch GPT-style LLM.

This Space serves the **chat** checkpoint: the 124M base model, instruction
tuned on Alpaca and Dolly and then trained on 20,000 UltraChat conversations,
so it answers a question and can be asked a follow-up about it.

Runs on a Hugging Face **ZeroGPU** Space, which attaches a GPU only for the
duration of a function decorated with `@spaces.GPU`. The model is therefore
loaded on CPU at startup and moved to CUDA inside the request handler. Expects
two files next to this one in the Space:
    model.pt              -- an inference-only checkpoint (scripts/export_inference.py)
    tokenizer_gpt2.json   -- a 69-byte descriptor, not a vocabulary

The descriptor names the GPT-2 tiktoken vocabulary these weights were fitted
to; tiktoken rebuilds the encoding from its own package data, so no vocabulary
file is shipped.

**Everything about the prompt format goes through InferenceService.** That is
deliberate rather than convenient: a chat model is only correct when it is
prompted in the exact document format it was trained on -- `### User:` /
`### Assistant:` markers, every prior turn replayed, the whole thing rendered
by `Conversation.render_for_generation()`. Re-implementing that string-building
here would give this Space its own copy to drift from, and the failure mode is
not a crash, it is fluent nonsense. The service is the same code path the
repo's own FastAPI server uses.
"""

from __future__ import annotations

from pathlib import Path

import gradio as gr
import spaces
import torch

from ashugpt.api.service import InferenceService

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "model.pt"
# A 69-byte descriptor naming the GPT-2 tiktoken vocabulary these weights were
# fitted to -- not a vocabulary file. load_tokenizer() reads the "type" field
# and rebuilds the encoding from tiktoken's own package data. It is shipped
# rather than hard-coding TiktokenBPETokenizer() here so that loading goes
# through the same tested path everything else in the repo uses: the two
# tokenizers have different id spaces, and the wrong one produces fluent
# nonsense rather than an error.
TOKENIZER_PATH = HERE / "tokenizer_gpt2.json"

# Loaded once at startup, on CPU -- ZeroGPU exposes CUDA only inside @spaces.GPU.
service = InferenceService.load(MODEL_PATH, TOKENIZER_PATH, device="cpu")

# 1.1 rather than 1.0 (off). Measured on the held-out Dolly split, this takes
# the instruction-tuned model's loop rate from 20% to 8% while its stop rate
# rises 92% -> 98% -- the only setting in the sweep that improves both. Past
# it, looping keeps falling and stopping collapses (48% at 1.5), so this is a
# bracketed choice and not "more is better".
DEFAULT_REPETITION_PENALTY = 1.1


@spaces.GPU
def respond(
    message: str,
    history: list[dict],
    max_new_tokens: int,
    temperature: float,
    top_k: int,
    repetition_penalty: float,
) -> str:
    message = (message or "").strip()
    if not message:
        return "Ask me something to get started."

    device = "cuda" if torch.cuda.is_available() else "cpu"
    service.model.to(device)
    service.device = device

    # Gradio's messages format is [{"role": ..., "content": ...}]; the service
    # wants (role, content) pairs. Anything that is not a user/assistant turn
    # is dropped rather than guessed at.
    turns = [
        (m["role"], m["content"])
        for m in (history or [])
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    result = service.generate(
        prompt=message,
        max_new_tokens=int(max_new_tokens),
        temperature=float(temperature),
        top_k=int(top_k) if top_k > 0 else None,
        top_p=None,
        repetition_penalty=float(repetition_penalty),
        chat=True,
        history=turns,
    )
    return result.generated_text.strip() or "(the model produced nothing)"


DESCRIPTION = """
A decoder-only transformer — **RoPE, RMSNorm, SwiGLU, causal attention with a
KV cache** — implemented from scratch in PyTorch, with no `transformers`.
Pretrained from random initialization on **2.46B tokens** of FineWeb-Edu
(validation perplexity **23.53**, ~27 hours on a single RTX 2080 Ti), then
instruction tuned on Alpaca and Dolly, then trained on 20,000 UltraChat
conversations so it holds a **multi-turn** conversation — every earlier turn is
replayed to it in the format it was trained on, so follow-up questions work.

**It has 124M parameters and it is frequently, confidently wrong.** That is
what this size buys: fluent form and unreliable content are separate axes, and
the repo measures both rather than hiding the gap. It is a demo of a training
stack, not a source of facts.

Want the **base model** — the pretrained checkpoint before any fine-tuning,
which continues text rather than answering? The weights are downloadable at
👉 **[AuthRan/AshuGPT-124M-base](https://huggingface.co/AuthRan/AshuGPT-124M-base)**

[Code](https://github.com/AuthRan/AuthLLM) ·
[What it writes, unedited](https://github.com/AuthRan/AuthLLM/tree/main/results) ·
[How it was built](https://github.com/AuthRan/AuthLLM/tree/main/learning)
"""

demo = gr.ChatInterface(
    fn=respond,
    type="messages",
    title="AshuGPT — a 124M chat model built from scratch",
    description=DESCRIPTION,
    additional_inputs=[
        gr.Slider(16, 400, value=200, step=8, label="Max new tokens"),
        gr.Slider(0.0, 1.5, value=0.8, step=0.05, label="Temperature (0 = greedy)"),
        gr.Slider(0, 200, value=50, step=1, label="Top-k (0 = off)"),
        gr.Slider(
            1.0,
            1.5,
            value=DEFAULT_REPETITION_PENALTY,
            step=0.05,
            label="Repetition penalty (1.0 = off; 1.1 measured best)",
        ),
    ],
    examples=[
        ["What is photosynthesis?"],
        ["Give me three tips for learning to code."],
        ["Explain why the sky is blue."],
        ["What causes earthquakes?"],
    ],
    cache_examples=False,
)

if __name__ == "__main__":
    demo.launch()
