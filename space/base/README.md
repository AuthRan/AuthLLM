---
title: AshuGPT Base
emoji: 📖
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 5.14.0
app_file: app.py
pinned: false
license: mit
---

# AshuGPT — a GPT built from scratch

Live demo of a decoder-only transformer implemented from scratch in PyTorch
(RoPE, RMSNorm, SwiGLU, causal attention with a KV cache), pretrained from
random initialization on 2.46B tokens of FineWeb-Edu. 124M parameters,
validation perplexity 23.53, trained on a single RTX 2080 Ti.

It is a base model -- it continues text rather than answering questions, and
its facts are unreliable at this size. **If you want one that answers
questions and holds a conversation**, the fine-tuned model is at
https://huggingface.co/spaces/AuthRan/AshuGPT-chat -- same weights underneath,
plus instruction tuning, chat training and preference tuning. Sample output and an honest read on what
it did and did not learn: https://github.com/AuthRan/AuthLLM/tree/main/results

Source: https://github.com/AuthRan/authLLM

## Files this Space needs

Drop one file next to `app.py` in the Space repo:

- `model.pt` — an inference-only checkpoint, produced by
  `scripts/export_inference.py` (494MB for the 124M model, down from 1.5GB once
  optimizer state is stripped). Needs git-LFS on the Space.

No `tokenizer.json`: the 124M weights were fitted to the GPT-2 tiktoken
vocabulary, which the tokenizer rebuilds from its own package data.
