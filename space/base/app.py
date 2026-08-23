"""Gradio demo for AshuGPT -- a from-scratch GPT-style LLM.

Runs on a Hugging Face **ZeroGPU** Space (the only free hardware for Gradio
Spaces). ZeroGPU attaches a GPU only for the duration of a function decorated
with `@spaces.GPU`, so the model is loaded on CPU at startup and moved to CUDA
inside the request handler. Expects one file next to this one in the Space:
    model.pt        -- an inference-only checkpoint (scripts/export_inference.py)

No tokenizer file is needed: the 124M model was trained on the GPT-2 tiktoken
vocabulary, which the tokenizer reconstructs from its own package data. The
from-scratch BPETokenizer is still what tiny/small were trained with, and is
still the educational centrepiece -- it is simply not the vocabulary these
weights were fitted to, and loading the wrong one would produce fluent-looking
nonsense rather than an error.

The model is reconstructed entirely from the checkpoint's own saved config,
so nothing here is hard-coded to a particular size.
"""

from __future__ import annotations

from pathlib import Path

import gradio as gr
import spaces
import torch

from ashugpt.inference.generate import generate
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training.checkpoint import load_model_for_inference

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "model.pt"

# Loaded once at startup, on CPU (ZeroGPU exposes CUDA only inside @spaces.GPU).
tokenizer = TiktokenBPETokenizer()
model = load_model_for_inference(MODEL_PATH)
model.eval()


@spaces.GPU
def run(prompt: str, max_new_tokens: int, temperature: float, top_k: int, top_p: float) -> str:
    prompt = prompt.strip()
    if not prompt:
        return "Type a prompt to get started."

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)

    input_ids = torch.tensor([tokenizer.encode(prompt, add_bos=True)], device=device)
    output_ids = generate(
        model,
        input_ids,
        max_new_tokens=int(max_new_tokens),
        temperature=float(temperature),
        top_k=int(top_k) if top_k > 0 else None,
        top_p=float(top_p) if 0.0 < top_p < 1.0 else None,
        eos_id=tokenizer.eos_id,
    )
    return tokenizer.decode(output_ids[0].tolist())


demo = gr.Interface(
    fn=run,
    inputs=[
        gr.Textbox(label="Prompt", value="The process of photosynthesis", lines=2),
        gr.Slider(16, 256, value=120, step=8, label="Max new tokens"),
        gr.Slider(0.0, 1.5, value=0.8, step=0.05, label="Temperature (0 = greedy)"),
        gr.Slider(0, 200, value=50, step=1, label="Top-k (0 = off)"),
        gr.Slider(0.0, 1.0, value=1.0, step=0.05, label="Top-p (1 = off)"),
    ],
    outputs=gr.Textbox(label="Generated text", lines=12),
    title="AshuGPT — a 124M GPT built from scratch",
    description=(
        "A decoder-only transformer (RoPE, RMSNorm, SwiGLU, KV-cache) implemented from "
        "scratch in PyTorch, pretrained from random initialization on 2.46B tokens of "
        "FineWeb-Edu on a single RTX 2080 Ti. Validation perplexity 23.53.\n\n"
        "This is a *base* model: it continues text, it does not answer questions or follow "
        "instructions. It writes fluent educational prose and is frequently confidently "
        "wrong about facts, which is what 124M parameters buys. Expository prompts work "
        "best. Low temperatures make it repeat itself."
    ),
    examples=[
        ["The process of photosynthesis", 120, 0.8, 50, 1.0],
        ["A volcano erupts when", 120, 0.8, 50, 1.0],
        ["Children learn to read by", 120, 0.8, 50, 1.0],
        ["The water cycle begins when", 120, 0.8, 50, 1.0],
    ],
    cache_examples=False,
)

if __name__ == "__main__":
    demo.launch()
