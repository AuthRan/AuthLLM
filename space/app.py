"""Gradio demo for AshuGPT -- a from-scratch GPT-style LLM.

Runs on a Hugging Face Space (CPU is fine for this ~14M model). Expects two
files sitting next to it in the Space repo:
    model.pt        -- a training checkpoint (rename your best step_N.pt to this)
    tokenizer.json  -- the from-scratch BPE tokenizer trained alongside it

The model is reconstructed entirely from the checkpoint's own saved config,
so nothing here is hard-coded to a particular size.
"""

from __future__ import annotations

from pathlib import Path

import gradio as gr

from ashugpt.inference.generate import generate_text
from ashugpt.tokenizer import BPETokenizer
from ashugpt.training.checkpoint import load_model_for_inference

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "model.pt"
TOKENIZER_PATH = HERE / "tokenizer.json"

# Loaded once at startup, reused across every request.
tokenizer = BPETokenizer.load(TOKENIZER_PATH)
model = load_model_for_inference(MODEL_PATH)
model.eval()


def run(prompt: str, max_new_tokens: int, temperature: float, top_k: int, top_p: float) -> str:
    prompt = prompt.strip()
    if not prompt:
        return "Type a prompt to get started."
    return generate_text(
        model,
        tokenizer,
        prompt,
        max_new_tokens=int(max_new_tokens),
        temperature=float(temperature),
        top_k=int(top_k) if top_k > 0 else None,
        top_p=float(top_p) if 0.0 < top_p < 1.0 else None,
    )


demo = gr.Interface(
    fn=run,
    inputs=[
        gr.Textbox(label="Prompt", value="Once upon a time", lines=2),
        gr.Slider(16, 256, value=120, step=8, label="Max new tokens"),
        gr.Slider(0.0, 1.5, value=0.8, step=0.05, label="Temperature (0 = greedy)"),
        gr.Slider(0, 200, value=50, step=1, label="Top-k (0 = off)"),
        gr.Slider(0.0, 1.0, value=1.0, step=0.05, label="Top-p (1 = off)"),
    ],
    outputs=gr.Textbox(label="Generated story", lines=12),
    title="AshuGPT — a GPT built from scratch",
    description=(
        "A decoder-only transformer (RoPE, RMSNorm, SwiGLU, KV-cache) and a byte-level "
        "BPE tokenizer, both implemented from scratch in PyTorch and trained on TinyStories. "
        "Best with children's-story-style prompts."
    ),
    examples=[
        ["Once upon a time", 120, 0.8, 50, 1.0],
        ["The little robot", 120, 0.8, 50, 1.0],
        ["One day, a girl named Lily", 120, 0.8, 50, 1.0],
    ],
)

if __name__ == "__main__":
    demo.launch()
