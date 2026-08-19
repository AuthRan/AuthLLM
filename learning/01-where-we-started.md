# 1. Where We Started

## What already existed

AshuGPT was already a complete, working transformer written from scratch —
following *Build a Large Language Model (From Scratch)*, but implemented rather
than copied. Before this run, the repo had:

- **The model** (`ashugpt/model/`) — attention, RoPE, the whole stack, hand-written.
- **A tokenizer** (`ashugpt/tokenizer/`) — a real from-scratch BPE implementation.
- **A training loop** (`ashugpt/training/`) — forward, backward, LR schedule,
  gradient accumulation, mixed precision, checkpointing.
- **Inference** (`ashugpt/inference/`) — autoregressive decoding with a KV cache.
- **Four size presets** — `tiny`, `small`, `medium` (124M), `xl_1b`.
- **A live demo** on Hugging Face Spaces, running a `small` (~14M) model trained
  on TinyStories.
- **Tests**, including one that trains a tiny model for 150 steps and asserts the
  loss actually drops by 75%+.

So: the code was real, and it was tested.

## The gap

Here's the thing the README was already honest about, and it's worth restating
because the whole run exists to close it.

There are three very different claims that sound similar:

| Claim | What it actually means |
|---|---|
| "I implemented this architecture" | Code exists that can build it. The weights are random. It knows nothing. |
| "I trained this checkpoint" | Real gradient descent ran on real data and produced a real weights file. |
| "I loaded a pretrained checkpoint" | Someone else's weights. You didn't train anything. |

Before this branch, AshuGPT could honestly claim **#1 for all four presets** —
but only claim **#2 for `tiny` and `small`**, and only on small demo corpora.
The 124M `medium` preset had never had a single gradient step run through it.

That's a real gap. "I can construct a 124M model" is a much weaker statement
than "I trained a 124M model," and a 14M model on TinyStories doesn't prove
your code survives contact with a real corpus. Things that work fine at toy
scale — loading data into RAM, ignoring VRAM limits, not bothering to handle
crashes — all fall apart when a run takes a day and a half.

## The goal

Train the `medium` (123,587,328-parameter) model on real web-scale text, on the
hardware actually sitting under the desk: two RTX 2080 Ti cards, 11GB each.

Not to compete with GPT-2. To prove the from-scratch code genuinely works at a
scale where the difficulties are real.
