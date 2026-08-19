# 2. What We Did, and Why

The plan: train `medium` (124M params) on FineWeb-Edu, a high-quality
educational-web-text dataset. Prepare 5 billion tokens, run 20,000 steps.

Scaling from a 14M toy run to this meant five new pieces. Each one exists
because something in the old setup would have broken.

## 1. A streaming dataset (`ashugpt/data/sharded.py`)

**Why:** 5 billion tokens is ~10GB even at 2 bytes each. The old dataset loaded
everything into RAM, which is fine for TinyStories and impossible here.

**What:** A memory-mapped, sharded dataset. The OS pages data in from disk as
needed, so RAM usage stays flat no matter how big the corpus gets.

One subtle fix: the in-memory dataset used a default **stride of 1**, meaning
consecutive training examples shared 511 of their 512 tokens — you'd see the
same text over and over. Set `stride: 512` so one pass over the corpus is
genuinely one pass.

## 2. A GPT-2 BPE tokenizer (`ashugpt/tokenizer/tiktoken_bpe.py`)

**Why:** The from-scratch BPE tokenizer is the educational centerpiece, but
training a new vocabulary on 5B tokens would take ages and add nothing. Using
GPT-2's proven vocabulary makes the numbers comparable to published results.

**Note:** model vocab is 50,304 while the tokenizer's is 50,259 — 45 unused
padding rows, kept deliberately because a multiple of 64 is faster on tensor
cores.

## 3. A data prep script (`scripts/prepare_data.py`)

Downloads FineWeb-Edu, tokenizes it, writes it into shards with a manifest.
Final split: **4.9B train / 100M validation tokens**.

## 4. A supervised launcher (`scripts/run_medium_training.sh`)

**Why:** This was going to run for over a day. Something *will* go wrong.

**What:** Waits for data prep to finish, validates the manifest, then launches
training — and **resumes from the newest checkpoint** if restarted. This turned
out to matter enormously (see [challenge 4](03-challenges.md)).

## 5. A self-updating status block (`scripts/update_training_status.py`)

**Why:** This is a public portfolio repo. A README that says "training in
progress" is a *claim*. A README that regenerates itself from the run's own
metrics is *evidence*.

**What:** Reads the metrics CSV, checkpoint timestamps, and supervisor log, then
rewrites a block in README.md between two markers. Every number is derived, none
typed by hand. `sync_training_logs.sh` regenerates it, commits, and pushes on a
schedule.

---

## The config decisions worth remembering

We benchmarked *before* running, not after (`logs/benchmark_cuda.log`). Two
findings shaped everything.

### Why fp16 and not bf16

Conventional wisdom says prefer bf16 — no loss scaling needed, same exponent
range as fp32. **On this hardware that's wrong.** These are Turing (sm_75) cards
with fast fp16 tensor cores and *no native bf16*. Measured on a 4096×4096 matmul:

| precision | throughput |
|---|---|
| **fp16** | **57.3 TFLOP/s** |
| fp32 | 12.6 TFLOP/s |
| bf16 | 7.7 TFLOP/s |

bf16 is *emulated* here — 7.4× slower than fp16, and slower than plain fp32.

The trap: `torch.cuda.is_bf16_supported()` returns `True`. It tells you the
operation exists, not that it's fast. So: fp16 + GradScaler, and
`ashugpt/training/amp.py` now warns if a config gets this wrong.

### Why gradient checkpointing is OFF

It saves 44% of VRAM — genuinely useful when you're short. We weren't short at
this batch size, and it cost ~9% throughput (0.998 vs 0.915 s/step). Over 20,000
steps that's hours for nothing. **A memory optimization you don't need is just a
slowdown.**

### The batch arithmetic

```
tokens/step = batch_size × grad_accum_steps × num_gpus × seq_len
```

Planned: `8 × 30 × 2 × 512` = **245,760 tokens/step** → 20,000 steps ≈ 4.9B tokens.

That's the plan the config comments describe. [Challenge 3](03-challenges.md)
explains why the real run halved it — and why that was still fine.
