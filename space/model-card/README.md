---
license: mit
library_name: pytorch
pipeline_tag: text-generation
tags:
  - gpt
  - from-scratch
  - pytorch
  - rope
  - rmsnorm
  - swiglu
datasets:
  - HuggingFaceFW/fineweb-edu
---

# AshuGPT-124M-base

The **base** checkpoint of [AshuGPT](https://github.com/AuthRan/AuthLLM) — a
decoder-only transformer written from scratch in PyTorch, with no
`transformers`, no `AutoModel`, and no `Trainer`.

| | |
|---|---|
| Parameters | 123,587,328 |
| Trained on | 2.46B tokens of FineWeb-Edu |
| Steps | 20,000 |
| Validation loss / perplexity | 3.1583 / **23.53** |
| Hardware | one RTX 2080 Ti, ~27 hours, fp16 |
| Architecture | RoPE, RMSNorm, SwiGLU, causal attention with a KV cache |
| Tokenizer | GPT-2 tiktoken vocabulary (50,304 padded) |

## This is a base model

It **continues text**. It does not answer questions and does not follow
instructions, because nothing has taught it to yet — that is what the
fine-tuning stages in the repo do. Give it the start of an expository
paragraph, not a question.

If you want the model that answers and holds a conversation, it is live at
**[AshGPT](https://huggingface.co/spaces/AuthRan/AshGPT)**.

Its facts are unreliable at this size, and the repo measures that rather than
hedging it: fluent form and correct content are separate axes. Unedited samples
and an honest read on what it learned and what it did not are in
[`results/`](https://github.com/AuthRan/AuthLLM/tree/main/results).

## Usage

```bash
pip install git+https://github.com/AuthRan/AuthLLM.git tiktoken
```

```python
import torch
from huggingface_hub import hf_hub_download
from ashugpt.inference.generate import generate
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training.checkpoint import load_model_for_inference

path = hf_hub_download("AuthRan/AshuGPT-124M-base", "model.pt")
model = load_model_for_inference(path).eval()
tok = TiktokenBPETokenizer()

ids = torch.tensor([tok.encode("The process of photosynthesis", add_bos=True)])
out = generate(model, ids, max_new_tokens=120, temperature=0.8, top_k=50, eos_id=tok.eos_id)
print(tok.decode(out[0].tolist()))
```

`model.pt` is inference-only — the optimizer state is stripped by
`scripts/export_inference.py`, which is why it is 494MB rather than 1.5GB. The
model is rebuilt from the config saved inside the checkpoint, so nothing is
hard-coded to a particular size.

## How it was built

The full month-by-month record, including the failures, is in the repo:
[what went wrong](https://github.com/AuthRan/AuthLLM/blob/main/learning/03-challenges.md)
is the most useful file. Five failed launches, an OOM, a reboot that killed
everything, and a status reporter that published a confidently wrong ETA.
