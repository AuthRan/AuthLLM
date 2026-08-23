---
title: AshuGPT Chat
emoji: 💬
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 5.14.0
app_file: app.py
pinned: false
license: mit
---

# AshuGPT — a 124M chat model built from scratch

Live demo of a decoder-only transformer implemented from scratch in PyTorch
(RoPE, RMSNorm, SwiGLU, causal attention with a KV cache), pretrained from
random initialization on 2.46B tokens of FineWeb-Edu, then instruction tuned on
Alpaca and Dolly, then trained on 20,000 UltraChat conversations. 124M
parameters, base validation perplexity 23.53, trained on a single RTX 2080 Ti.

It answers questions and holds a **multi-turn** conversation: every earlier turn
is replayed to it in the `### User:` / `### Assistant:` format it was trained
on, so a follow-up that never names the subject still works.

Its facts are unreliable at this size, and that is measured rather than hedged
-- fluent form and correct content are separate axes. Unedited samples and an
honest read on what it did and did not learn:
https://github.com/AuthRan/AuthLLM/tree/main/results

**Want the base model** -- the pretrained checkpoint before any fine-tuning,
which continues text rather than answering? The weights are downloadable at
https://huggingface.co/AuthRan/AshuGPT-124M-base

Source: https://github.com/AuthRan/AuthLLM

## Files this Space needs

Next to `app.py` in the Space repo:

- `model.pt` -- an inference-only checkpoint, produced by
  `scripts/export_inference.py` (472MB for the 124M chat model, down from
  1.4GB once optimizer state is stripped). Needs git-LFS on the Space.
- `tokenizer_gpt2.json` -- a 69-byte descriptor naming the GPT-2 tiktoken
  vocabulary these weights were fitted to. Not a vocabulary file; tiktoken
  rebuilds the encoding from its own package data.
