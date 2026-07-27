---
title: AshuGPT
emoji: 📖
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 4.44.0
python_version: "3.11"
app_file: app.py
pinned: false
license: mit
---

# AshuGPT — a GPT built from scratch

Live demo of a decoder-only transformer and byte-level BPE tokenizer, both
implemented from scratch in PyTorch (RoPE, RMSNorm, SwiGLU, causal attention
with a KV cache) and trained on the TinyStories corpus.

Source: https://github.com/AuthRan/authLLM

## Files this Space needs

Drop these two next to `app.py` in the Space repo:

- `model.pt` — your chosen training checkpoint (rename your best `step_N.pt`)
- `tokenizer.json` — the BPE tokenizer trained alongside the model
