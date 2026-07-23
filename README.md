# AshuGPT

Educational, from-scratch GPT-style decoder-only LLM. See [SPEC.md](SPEC.md)
for the full design spec and milestone roadmap.

**Status: scaffolding + tokenizer + model + training pipeline + cached
generation + DDP + memory optimization + scaling/memory estimation done
(SPEC.md M0-M11 + M6 fully closed out).** Package structure, config
system, model-size presets, a from-scratch BPE tokenizer, the full
`AshuGPT` model, a complete training pipeline (DistributedDataParallel
included), autoregressive text generation (greedy/temperature/top-k/top-p,
KV-cached by default), five configurable memory optimizations, and a
memory estimator + `python -m ashugpt.inspect_model` CLI that can report
exact parameter count and estimated memory for any config — including
`xl_1b` (1.23B params) — without ever building an `nn.Module`, all exist
and are tested. Not yet built: on-disk/streaming data pipeline for a real
large corpus, FSDP/model parallelism, or the clean inference API class.

## Project Structure

```
authLLM/
├── SPEC.md                  # full design spec + milestone roadmap
├── README.md                 # this file
├── pyproject.toml            # package metadata, installs as `ashugpt`
├── requirements.txt          # dev/runtime dependencies
├── .gitignore
├── configs/
│   ├── model/                  # one YAML preset per model scale
│   │   ├── tiny.yaml            # ~7M params   — fast iteration, CPU seconds/step
│   │   ├── small.yaml           # ~30M params  — the real CPU-training target
│   │   ├── medium.yaml          # ~124M params — shape-tested, needs GPU to train
│   │   └── xl_1b.yaml           # ~1.23B params — shape-tested only, needs GPU
│   └── train/                  # one YAML preset per training run
│       ├── tiny_cpu.yaml         # a real (if slow) CPU run against a real corpus
│       └── synthetic_demo.yaml   # tuned to overfit the tiny synthetic corpus in seconds
├── scripts/
│   ├── train_tokenizer.py     # CLI: train a BPE tokenizer from a text file
│   ├── train.py                # CLI: train an AshuGPT model end to end
│   └── benchmark_memory.py     # memory/speed impact of each Milestone 9 optimization
├── ashugpt/                   # the installable package
│   ├── generate.py             # CLI: python -m ashugpt.generate --prompt "..."
│   ├── inspect_model.py        # CLI: python -m ashugpt.inspect_model --config 1b
│   ├── config.py               # ModelConfig + TrainConfig dataclasses + YAML loaders
│   ├── model/                  # transformer architecture (implemented)
│   │   ├── norm.py               # RMSNorm
│   │   ├── rope.py               # Rotary positional embeddings
│   │   ├── attention.py          # Causal multi-head self-attention
│   │   ├── feedforward.py        # SwiGLU feed-forward network
│   │   ├── block.py              # One decoder block (attn + ffn + norms + residuals)
│   │   └── gpt.py                # AshuGPT: embedding + N blocks + final norm + LM head
│   ├── tokenizer/
│   │   └── bpe_scratch.py       # from-scratch byte-level BPE tokenizer (implemented)
│   ├── data/
│   │   └── dataset.py           # load+tokenize, sliding-window chunking, train/val split
│   ├── training/
│   │   ├── optim.py              # AdamW factory + warmup/cosine LR schedule
│   │   ├── amp.py                # mixed-precision (autocast + GradScaler) helpers
│   │   ├── checkpoint.py         # save/resume (model + optimizer + step)
│   │   ├── ddp.py                # DistributedDataParallel setup/wrap/cleanup
│   │   └── trainer.py            # the training loop itself
│   ├── eval/
│   │   └── perplexity.py        # validation loop + perplexity
│   ├── inference/
│   │   └── generate.py          # sampling strategies + the autoregressive decoding loop
│   └── utils/
│       └── memory.py            # parameter-count-based memory estimator (no model built)
└── tests/
    ├── fixtures/
    │   ├── tiny_corpus.txt        # varied small corpus for tokenizer tests/demo
    │   └── synthetic_corpus.txt   # tiny, highly repetitive corpus for fast pipeline verification
    ├── unit/                     # one file per component (see ashugpt/ layout above)
    └── integration/
        ├── test_train_step.py    # proves the model actually learns (tiny overfitting test)
        ├── test_ddp.py           # real 2-process DDP run vs. single-process baseline
        └── _ddp_worker.py        # the script each DDP process actually runs (not a pytest file)
```

## Setup

```
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on Linux/Mac
pip install -e .
pip install -r requirements.txt
```

## Tests

```
pytest                    # everything
pytest tests/unit         # fast, run constantly
pytest tests/integration  # slower (~30s) -- an end-to-end proof the model can learn
```

## Model Config Presets

Each `configs/model/*.yaml` loads into a `ModelConfig` dataclass
(`ashugpt/config.py`). Shape constraints (e.g. `d_model` divisible by
`n_heads`) are validated at load time, so a broken preset fails immediately
with a clear error instead of deep inside a training run.

```python
from ashugpt.config import load_model_config

config = load_model_config("configs/model/small.yaml")
print(config.head_dim)              # 64
print(config.approx_param_count())  # ~29.9M
```

## Tokenizer

`ashugpt/tokenizer/bpe_scratch.py` is a from-scratch byte-level BPE
tokenizer (the same family of algorithm GPT-2/GPT-3 use) — see the module
docstring for how it works. Special tokens `<pad>`, `<bos>`, `<eos>`,
`<unk>` get fixed ids 0-3; `<unk>` is reserved but never actually produced,
since byte-level encoding has no out-of-vocabulary case.

Train one from a text file:

```
python scripts/train_tokenizer.py --input tests/fixtures/tiny_corpus.txt \
    --vocab-size 2000 --output tokenizer.json
```

Use it:

```python
from ashugpt.tokenizer import BPETokenizer

tok = BPETokenizer.load("tokenizer.json")
ids = tok.encode("Mia and Rex explored the forest.", add_bos=True, add_eos=True)
tok.decode(ids)  # "Mia and Rex explored the forest."

batch = tok.encode_batch(["short text", "a longer piece of text"], max_length=32)
# batch["input_ids"], batch["attention_mask"] -- ready for a DataLoader
```

A `tiktoken`-backed production wrapper behind the same interface (per
SPEC.md's hybrid tokenizer decision) is deferred to when the real training
pipeline needs it — not built this milestone.

## Model

`ashugpt/model/gpt.py` assembles everything above into `AshuGPT`:
token embedding -> `config.n_layers` x `TransformerBlock` -> final RMSNorm
-> LM head.

```python
import torch
from ashugpt.config import load_model_config
from ashugpt.model import AshuGPT

config = load_model_config("configs/model/tiny.yaml")
model = AshuGPT(config)

input_ids = torch.randint(0, config.vocab_size, (2, 16))
out = model(input_ids)
out.logits.shape  # (2, 16, vocab_size)

print(model.num_parameters())  # exact count; compare to config.approx_param_count()
```

**Next-token prediction and the shift**: `labels` is `input_ids` shifted one
position into the future, not the same text re-passed in --

```
input_ids: The  cat  sat
labels:    cat  sat  down
```

`logits[:, t, :]` (computed causally from `input_ids[:, :t+1]`) is compared
directly against `labels[:, t]` with no extra shifting inside the model —
the data pipeline is responsible for the shift when it slices a token
stream into windows (`input_ids = tokens[i:i+L]`, `labels = tokens[i+1:i+L+1]`).
Padding positions in `labels` should be set to `-100`, which
`F.cross_entropy` ignores by default:

```python
labels = torch.tensor([[264, 266, 270]])  # already the shifted targets
out = model(input_ids, labels=labels)
out.loss  # scalar
out.loss.backward()
```

## Training

Verify the whole pipeline in seconds against the tiny synthetic corpus:

```
python scripts/train_tokenizer.py --input tests/fixtures/synthetic_corpus.txt \
    --vocab-size 300 --output tokenizer.json
python scripts/train.py --model configs/model/tiny.yaml --train configs/train/synthetic_demo.yaml \
    --tokenizer tokenizer.json --input tests/fixtures/synthetic_corpus.txt \
    --checkpoint-dir checkpoints/demo --log-path training_log.csv
```

For a real (larger) corpus, use `configs/train/tiny_cpu.yaml` instead, and
a tokenizer/`--val-fraction` sized appropriately for it.

**Pipeline**: `load_and_tokenize()` turns a text file into one flat token
stream; `TokenizedDataset` slices it into overlapping fixed-length
`(input_ids, labels)` windows (see the Model section above for the shift);
a standard `DataLoader` batches them — no custom `collate_fn` needed, since
every window is already the same fixed length.

**Training loop** (`ashugpt/training/trainer.py`), one iteration:

```python
lr = get_lr(step, config)                              # scheduler step
for group in optimizer.param_groups: group["lr"] = lr

optimizer.zero_grad(set_to_none=True)                   # gradient reset

for _ in range(config.grad_accum_steps):
    with autocast_context(device.type, amp_dtype):       # mixed precision
        output = model(input_ids, labels=labels)         # forward pass
        loss = output.loss / config.grad_accum_steps     # loss calculation
    scaler.scale(loss).backward()                         # backward pass, gradient-scaled if amp_dtype=float16

scaler.unscale_(optimizer)
torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)  # gradient clipping

scaler.step(optimizer)                                   # optimizer step
scaler.update()
```

`scaler` always exists (`build_grad_scaler`) but is only *enabled* for
`amp_dtype="float16"` — bf16 doesn't need loss scaling (same exponent range
as fp32), so under bf16 or no AMP, `scaler.scale/unscale_/step/update` are
all no-ops and the code above runs unchanged. Every `grad_accum_steps`
micro-batches share one optimizer step, with the loss divided by
`grad_accum_steps` first so accumulated gradients average correctly.

**Validation + perplexity**: `evaluate()` runs a no-grad pass over
`val_loader`, averages the loss, and reports `exp(loss)` as perplexity — a
random-init model should sit near `vocab_size`; the overfitting test drives
it down toward 1.

**Checkpointing**: `save_checkpoint`/`load_checkpoint` persist model
weights, optimizer state, and the step count together via
`torch.save`/`torch.load(weights_only=True)` — see `ashugpt/training/checkpoint.py`'s
docstring for why this (rather than literal safetensors) is the right
format for a *resumable* checkpoint specifically.

**Proof it learns**: `tests/integration/test_train_step.py` trains a tiny
model on `tests/fixtures/synthetic_corpus.txt` (a handful of short
sentences repeated many times) for 150 steps and asserts the loss drops by
more than 75% — in practice it goes from ~5.5 (near `ln(vocab_size)`,
i.e. random guessing) to well under 0.1.

## Generation

```
python -m ashugpt.generate --checkpoint checkpoints/demo/step_150.pt \
    --tokenizer tokenizer.json --prompt "Once upon a time" \
    --max-new-tokens 50 --temperature 0.8 --top-k 50 --top-p 0.9

# Greedy decoding (deterministic, no randomness at all):
python -m ashugpt.generate --checkpoint ... --tokenizer ... --prompt "..." --temperature 0.0
```

`ashugpt/inference/generate.py` implements the decoding loop **with a KV
cache by default** (`use_cache=True`; pass `False` or CLI `--no-cache` for
the naive baseline). See the "KV Cache" section below for how caching
changes what gets run each step:

```python
for _ in range(max_new_tokens):
    logits = model(generated).logits[:, -1, :]        # 1. run the model, 2. final-token logits
    next_tokens = sample_next_token(logits, ...)        # 3. logits -> probabilities -> 4. pick a token
    generated = torch.cat([generated, next_tokens], 1)   # 5. append
    if eos hit for every row: break                       # 6. stop at max length or EOS
```

**logits → temperature → softmax → top-k → top-p, in order:**

- **Logits** are the model's raw, unnormalized output scores over the
  vocabulary — one real number per token, computed by `lm_head`. They
  aren't probabilities yet: they can be negative, and don't sum to 1.
- **Temperature scaling** divides logits by `T` *before* softmax. `T < 1`
  spreads the gap between the largest and everything else further apart
  (sharper, more confident distribution → more repetitive/conservative
  text); `T > 1` compresses the gaps (flatter distribution → more random/
  diverse text). `T = 0` is undefined for this formula (division by zero),
  so it's handled as its own case: greedy decoding, just take the argmax.
- **Softmax** turns (temperature-scaled) logits into an actual probability
  distribution: `exp(logit_i) / sum(exp(logit_j))` — non-negative, sums to
  1, and preserves the *ranking* of the logits (softmax is monotonic) while
  temperature controls how *peaked* that distribution is.
- **Top-k** throws away every token outside the k highest logits *before*
  softmax normalizes what's left — a hard cutoff on vocabulary breadth,
  regardless of how the probability mass happens to be shaped. Good at
  preventing a long tail of implausible tokens, but k is fixed even when
  the model is very confident (few good options) or very unsure (many
  plausible options).
- **Top-p (nucleus)** instead keeps the *smallest* set of highest-probability
  tokens whose probabilities sum to at least `p`, so the cutoff adapts to
  the model's confidence at that specific position: a peaked distribution
  keeps very few tokens, a flat one keeps many. `top_k` and `top_p` can be
  combined (top-k narrows the field first, top-p then trims that further).

**Avoiding invalid probabilities**: `top_k`/`top_p` always leave at least
one token unmasked per row (top-k keeps ≥1 by construction; top-p always
keeps the single most-likely token even if its own probability already
exceeds `p`), so `softmax` can never collapse a row to all `-inf` → NaN.
`temperature`/`top_k`/`top_p` are validated (must be ≥0, positive, and in
`(0, 1]` respectively) and rejected early with a clear error otherwise.

**Batching + EOS**: `generate()` accepts `input_ids` of shape `(batch,
prompt_len)`. Rows that emit `eos_id` are pinned to keep "generating"
`eos_id` for every later position (via `torch.where` on a per-row
`finished` mask) rather than left to keep sampling — this keeps the output
tensor rectangular without corrupting a finished row's content, and
`tokenizer.decode()` already strips every special token regardless of
position, so no separate trimming step is needed. The whole loop breaks
early once every row in the batch has finished, not just when max
generation length is hit.

## KV Cache

Without caching, generating token *t* re-runs attention over all *t*
earlier tokens *again*, even though their keys/values never change once
computed — the model already predicted them correctly on every previous
step. A KV cache just remembers those keys/values instead of recomputing
them.

The mechanism itself (`CausalSelfAttention`/`TransformerBlock`/`AshuGPT`
all accept `kv_cache`/`position_offset` and return the updated cache) has
existed since Milestones 3-4 — it was built then specifically so this
milestone would be pure wiring, not new attention math. `generate()` now
actually uses it:

```python
# batch=B, n_heads=H, head_dim=D, d_model=H*D, vocab=V, prompt length=P

# First call: the whole prompt at once
output = model(input_ids, kv_caches=None, position_offset=0)
# input_ids:            (B, P)
# per-layer Q, K, V:    (B, H, P, D)          -- computed for every prompt position
# output.kv_caches[i]:  (B, H, P, D), (B, H, P, D)   -- one (k, v) pair per layer
# output.logits:        (B, P, V)

# Every step after that: just the ONE new token
output = model(next_token, kv_caches=kv_caches, position_offset=cache_len)
# next_token:            (B, 1)
# per-layer Q, K, V for *just this token*: (B, H, 1, D)
# inside attention: k = cat(cached_k, new_k) -> (B, H, cache_len+1, D); same for v
# attention scores: (B,H,1,D) @ (B,H,D,cache_len+1) -> (B, H, 1, cache_len+1)
#   -- the one new query attends over every cached position plus itself
# output.kv_caches[i]:   (B, H, cache_len+1, D), ...   -- cache grew by 1
# output.logits:         (B, 1, V)
```

`position_offset` must always equal the absolute position of the input's
*first* token, so RoPE rotates it correctly (position 0 for the initial
prompt call; `cache_len` for every call after, since that's exactly where
the new token sits once everything cached so far is accounted for).

**Correctness, not speed, is the point of this milestone** — both paths
must produce the same tokens:

```python
out_cached   = generate(model, prompt, max_new_tokens=50, temperature=0.0, use_cache=True)
out_uncached = generate(model, prompt, max_new_tokens=50, temperature=0.0, use_cache=False)
assert torch.equal(out_cached, out_uncached)  # true -- greedy decoding is fully deterministic
```

Verified two ways: greedy decoding (no randomness at all) gives
byte-identical tokens; comparing raw logits directly between one full
forward pass and the equivalent sequence of cached incremental calls shows
~1e-7 max absolute difference (ordinary float32 op-order noise, not a
correctness gap). A real (if modest, at this tiny scale) speedup shows up
too — 1.5-1.7x by 150 generated tokens on the `tiny` model config, growing
with generation length as expected, since caching removes exactly the O(n)
redundant recomputation per step that the uncached path does.

No pre-allocated cache buffer or other speed optimization was added beyond
that — deliberately, since the milestone's own instruction was to verify
correctness first and not optimize further yet.

## Distributed Training (DDP)

`scripts/train.py` needs no flags to become distributed — the same command
becomes multi-process just by how it's launched:

```
# Single process (unchanged from before):
python scripts/train.py --model configs/model/tiny.yaml --train configs/train/tiny_cpu.yaml \
    --tokenizer tokenizer.json --input corpus.txt --checkpoint-dir checkpoints/run1

# Multi-GPU (or multi-process-on-CPU), 2 processes on one machine:
torchrun --nproc_per_node=2 scripts/train.py --model configs/model/tiny.yaml \
    --train configs/train/tiny_cpu.yaml --tokenizer tokenizer.json --input corpus.txt \
    --checkpoint-dir checkpoints/run1

# Multiple machines, e.g. 2 nodes x 4 GPUs each:
torchrun --nnodes=2 --nproc_per_node=4 --rdzv_id=100 --rdzv_backend=c10d \
    --rdzv_endpoint=<master-node-ip>:29500 scripts/train.py --model ... --train ...
```

`torchrun` sets `RANK`/`LOCAL_RANK`/`WORLD_SIZE`/`MASTER_ADDR`/`MASTER_PORT`
in every process's environment; `ashugpt/training/ddp.py::setup_distributed()`
reads those and does the rest. Not launched via `torchrun`? `WORLD_SIZE` is
unset, `setup_distributed()` returns a world_size=1 `DistributedInfo`
without touching `torch.distributed` at all, and every `if
info.is_distributed:` branch in `trainer.py` is simply skipped — single-GPU
and multi-GPU are the same code path, not two implementations.

> **Windows note**: this project's dev/test environment (a CPU-only torch
> wheel on Windows) hits a `torchrun`/`torch.distributed.run` rendezvous
> bug unrelated to this code (`TCPStore` built without libuv support) — see
> SPEC.md's M11 entry. `tests/integration/test_ddp.py` works around it by
> launching processes directly with the same env vars torchrun would set,
> which exercises identical `dist.init_process_group()` code. If you hit
> this on a real deployment, that manual-launch pattern is the fallback;
> on a normal Linux/CUDA setup, plain `torchrun` is expected to just work.

### What happens during one training step across multiple GPUs

```
GPU 0 (rank 0)                          GPU 1 (rank 1)
--------------                          --------------
model replica (weights identical --     model replica (weights identical --
  broadcast from rank 0 at DDP            broadcast from rank 0 at DDP
  wrap time)                              wrap time)
batch shard (its 1/world_size of        batch shard (a *different*
  this step's data, via                   1/world_size, via
  DistributedSampler)                     DistributedSampler)

forward pass  -> local loss             forward pass  -> local loss
backward pass -> local gradients        backward pass -> local gradients
        \                                      /
         \____________  DDP all-reduce  ______/
                      \/
      every GPU's gradients are averaged together;
      after this, every replica holds the *same*
      (mean) gradient for every parameter
                      /\
         ____________/  \____________
        /                             \
optimizer.step()                 optimizer.step()
(applies the identical            (applies the identical
 averaged gradient)                averaged gradient)
        \                             /
         \___________________________/
      every replica is still identical --
      DDP doesn't need to re-sync weights,
      only gradients, because they started
      identical and applied identical updates
```

The all-reduce is triggered automatically by autograd hooks the moment
`loss.backward()` finishes on every rank — there's no explicit "sync
gradients" call anywhere in `trainer.py`; it's built into what
`DistributedDataParallel` wraps `.backward()` with. The one place this
*does* need explicit handling is **gradient accumulation**: DDP
synchronizes on every `.backward()` by default, which is correct but
wasteful mid-accumulation-window (you only need the final, fully-summed
gradient, not an average-of-partial-sums at every micro-step). `trainer.py`
wraps every micro-step except the last in `model.no_sync()`, so the
all-reduce only actually happens once per optimizer step, on the last
micro-batch — the same number of communications regardless of
`grad_accum_steps`.

### The 9 requirements

| # | Requirement | Where |
|---|---|---|
| 1 | Process initialization | `setup_distributed()` — `dist.init_process_group(backend=...)` |
| 2 | Rank/world-size handling | Read from `RANK`/`WORLD_SIZE`/`LOCAL_RANK` env vars into a `DistributedInfo` |
| 3 | DDP wrapping | `wrap_model_for_ddp()` — moves to device, wraps in `DistributedDataParallel` if `world_size > 1` |
| 4 | DistributedSampler | Built in `train()`, `shuffle=True`, `seed=config.seed`; `set_epoch()` called every epoch boundary so shuffling actually differs epoch to epoch (a well-known easy-to-forget DDP correctness detail) |
| 5 | Device assignment | `nccl` → `cuda:{local_rank}`; `gloo` → `cpu`; same `wrap_model_for_ddp()` call either way |
| 6 | Synchronization | Automatic on `.backward()` via DDP; explicitly *deferred* (not skipped) during grad-accum micro-steps via `no_sync()` |
| 7 | Rank-zero-only logging | Every `print`/history-append/CSV-write gated on `info.is_main_process` |
| 8 | Rank-zero-only checkpointing | Same gate, plus always saves `unwrap_model(model)` — DDP's own `state_dict()` prefixes every key with `"module."`, which would silently break loading a distributed-trained checkpoint into a plain model for inference later if not unwrapped first |
| 9 | Process group cleanup | `cleanup_distributed()` runs in a `finally` block around the whole training loop, so it fires even if training raises |

**Resuming** works identically distributed or not: every rank
independently reads the same checkpoint file from shared/local disk (simplest
correct approach for single-node multi-GPU) via `unwrap_model(model)`, so
the loaded state always matches regardless of which mode originally saved
it. **Gradient accumulation** and **mixed precision** are exactly the
Milestone 5 implementations, unmodified — `no_sync()` (above) is the only
place DDP and grad-accum actually interact; AMP's `autocast`/`GradScaler`
don't care how many processes are running.

### Verifying it actually works: a real 2-process test

`tests/integration/test_ddp.py` launches two real, independent OS
processes running `_ddp_worker.py` (gloo backend), on a fixed 8-example
dataset split evenly 4-and-4 across the two ranks. Two things are checked:

1. **The ranks stay in sync**: despite training on *disjoint* data shards,
   both ranks' final weights are `torch.equal` — bit-identical. That's only
   possible if gradients were actually averaged across ranks before the
   optimizer step; if synchronization were broken, each rank would drift
   toward whatever its own local shard implies and diverge.
2. **The result matches a single-process baseline, exactly**: with equal
   per-rank shard sizes, DDP's cross-rank gradient average mathematically
   equals a single-process gradient computed over the whole combined
   dataset as one batch (`mean(mean_A, mean_B) == mean(A ∪ B)` when
   `|A| == |B|`) — not an approximation, algebra. A plain, non-distributed
   `train()` call over all 8 examples at once is run directly in the test
   process and compared against the DDP result within `atol=1e-5`.

A third check confirms rank-zero-only behavior directly: the captured
stdout from both processes combined contains exactly one `"train_loss"`
log line and exactly one `"Saved checkpoint"` line (not two), and exactly
one checkpoint file exists on disk.

Also manually verified with a full 150-step run of `scripts/train.py`
through the real CLI (2 processes, `tiny` model, real synthetic-corpus
data): correct convergence (loss ~5.5 → ~0.08, matching the single-process
result), exactly one checkpoint saved. It took noticeably longer than
single-process (~140s vs. ~10s) — on CPU/gloo, an all-reduce happens on
every single optimizer step regardless of how small the model is, so at
this tiny scale the run is communication-overhead-bound, not
compute-bound. That's expected and matches DDP's usual tradeoff profile:
it pays off when per-step compute is large relative to the fixed
communication cost (bigger models, bigger batches, or NCCL/GPU instead of
gloo/CPU), not on a 7M-parameter model doing 150 tiny steps.

Not implementing FSDP or model parallelism.

## Memory Optimization

Five configurable levers, each addressing a *different* part of a training
run's memory footprint. All live on `TrainConfig` and get applied inside
`train()` via `AshuGPT.set_memory_optimizations()` / `build_optimizer()` --
none require touching `ModelConfig` (the architecture itself doesn't
change; only how much memory computing it takes does):

```yaml
gradient_checkpointing: true
amp_dtype: bfloat16          # SPEC calls this "mixed_precision"
gradient_accumulation_steps: 8   # TrainConfig field name: grad_accum_steps
use_efficient_attention: true
optimizer: sgd                # or "adamw" (default)
```

`scripts/benchmark_memory.py` measures every one of them (peak process RSS
+ per-step wall-clock time, via `psutil`, each scenario in its own fresh
subprocess so PyTorch's CPU allocator cache from one run can't inflate the
next one's reading):

```
python scripts/benchmark_memory.py
```

Actual measured results (`benchmark` config: 5.4M params, d_model=256,
6 layers, 8 heads, seq_len=256, 1 step; CPU/no-GPU environment):

| scenario | peak RSS (MB) | vs. baseline | s/step |
|---|---:|---:|---:|
| baseline | 757.0 | — | 3.44 |
| gradient_checkpointing | 467.1 | **-38.3%** | 4.11 |
| mixed_precision (bf16) | 595.7 | -21.3% | **59.62** |
| grad_accum_x8 (same effective batch) | 399.9 | **-47.2%** | 3.64 |
| efficient_attention | 624.7 | -17.5% | 3.56 |
| sgd_optimizer | 756.4 | -0.1% | 3.37 |
| all_combined | 371.5 | **-50.9%** | 66.68 |

### 1. Gradient checkpointing

**Problem**: a normal forward pass keeps every intermediate activation
(the output of every Linear, every attention score matrix, etc.) alive in
memory, because backward() needs them to compute gradients. That's
`O(n_layers x seq_len x batch_size x d_model)`-ish memory, and it's the
single biggest consumer of training memory once you get past a few layers.

**Fix**: discard activations after each block's forward pass and
*recompute* them during backward instead of storing them --
`torch.utils.checkpoint.checkpoint(block, x, ..., use_reentrant=False)`
wraps each `TransformerBlock`. `use_reentrant=False` is the modern,
DDP-safe mode (the older reentrant mode has known issues with DDP's
gradient hooks). Only engaged when it can actually help: `self.training`
(no backward pass during generation, so no point) and `kv_caches is None`
(checkpointing re-runs a block's forward from scratch, which can't be
reconciled with a growing cache -- and generation never has a backward
pass to checkpoint for anyway).

**Tradeoff**: roughly one extra forward pass per checkpointed block during
backward -- you're trading compute (recompute) for memory (don't store).

**Measured**: **-38.3% peak RSS** (757.0MB → 467.1MB), for a modest +19%
per-step time cost (3.44s → 4.11s) from the extra recompute — exactly the
tradeoff the theory predicts, and the single biggest memory win of any
individual lever measured here (only beaten by stacking everything
together in `all_combined`, -50.9%).
Verified exact (not approximate) equivalence first, since
"faster and correct" only matters after "correct":
`test_gradient_checkpointing_matches_normal_forward_and_gradients` checks
logits, loss, *and every parameter's gradient* match the non-checkpointed
run within float32 tolerance.

**Configurable**: `TrainConfig.gradient_checkpointing`, or directly via
`model.set_memory_optimizations(gradient_checkpointing=True)`.

### 2. Mixed precision

**Problem**: fp32 activations and weights take 4 bytes per element; on
hardware with fast lower-precision compute, that's both more memory
*and* slower than necessary.

**Fix**: `torch.autocast` runs most ops in bf16 (or fp16) while keeping a
few numerically-sensitive ops (like softmax accumulation) in fp32
internally — implemented in `ashugpt/training/amp.py`, already built in
Milestone 5.

**Tradeoff**: bf16 needs no loss scaling (same exponent range as fp32,
just less mantissa precision) but *does* need hardware that actually
computes bf16 efficiently. fp16 has a narrower exponent range and needs
`GradScaler` to avoid gradients underflowing to zero — already wired up
(disabled unless `amp_dtype="float16"`, so it's a no-op in the bf16 case).

**Measured**: **-21.3% peak RSS** (757.0MB → 595.7MB) — bf16 tensors
genuinely are smaller — but **17x slower per step** (3.44s → 59.62s) in
the full benchmark. This is the sharpest lesson in this whole milestone:
mixed precision's benefit is *hardware-dependent*, not universal. On a
GPU with tensor cores, or a CPU with native bf16 instructions
(AVX512-BF16, or equivalent), bf16 is both faster and lower memory. On
this benchmark's CPU, which lacks that hardware support, bf16 autocast
falls back to a much slower emulated path — an isolated single
forward+backward at the original (larger, seq_len=512) benchmark size
measured a **~44x slowdown** (134.5s vs 3.0s), which is exactly why the
benchmark script had to shrink its problem size to stay tractable at all.
A real consequence worth knowing before assuming "bf16 = free speedup" —
check your actual hardware, not just the theory.

**Configurable**: `TrainConfig.amp_dtype` (`"bfloat16"` / `"float16"` / `"none"`).

### 3. Efficient attention

**Problem**: the manual attention implementation (Milestone 3) explicitly
materializes the full `(seq_len, seq_len)` score matrix per head as its
own tensor — `O(seq_len^2)` memory, and on GPU, extra round-trips through
memory bandwidth that a fused kernel avoids.

**Fix**: an optional path through `torch.nn.functional.scaled_dot_product_attention`
(`CausalSelfAttention.use_efficient_attention`), reusing the *exact same*
`causal_mask()` this project already had, just converted to SDPA's
additive-bias convention. On GPU this dispatches to a FlashAttention-style
kernel with `O(seq_len)` attention-matrix memory instead of `O(seq_len^2)`;
on CPU it's mainly a fused-kernel/less-Python-overhead win.

**Tradeoff**: less transparent — the manual path stays the default
specifically so the actual math (score, mask, softmax, weighted sum)
remains visible and directly testable; the efficient path is opt-in for
when it actually matters.

**Measured**: **-17.5% peak RSS** (757.0MB → 624.7MB) at essentially the
same speed (3.44s → 3.56s) — a real, "free" win even on CPU, from not
allocating the score matrix as its own separate tensor; expect a much
larger memory reduction specifically on GPU, where SDPA also gets to
choose an `O(seq_len)`-memory FlashAttention-style kernel instead of just
avoiding one intermediate CPU tensor. Correctness first, as always: `test_attention.py`
checks the efficient path matches the manual path's output
(`atol=1e-5`), still can't attend to future tokens, and stays correct
under KV-cache incremental calls — the same three properties already
proven for the manual path.

**Configurable**: `TrainConfig.use_efficient_attention`, or directly via
`model.set_memory_optimizations(efficient_attention=True)`.

### 4. Gradient accumulation

**Problem**: peak activation memory scales with the *per-step* batch
size. Wanting the gradient quality/statistics of a large effective batch
collides with only having memory for a small one.

**Fix**: split the target effective batch into `grad_accum_steps` smaller
micro-batches, summing their (divided) losses' gradients before one
optimizer step — already built in Milestone 5, `no_sync()`-aware for DDP
since Milestone 8.

**Tradeoff**: more forward/backward passes (more wall-clock time) to
reach the same effective batch size, in exchange for peak memory bounded
by the *micro*-batch size, not the effective one.

**Measured**: **-47.2% peak RSS** (757.0MB → 399.9MB) at essentially the
same per-step time (3.44s → 3.64s) — comparing `batch_size=8,
grad_accum_steps=1` against the same *effective* batch size of 8 via
`batch_size=1, grad_accum_steps=8`. The second-biggest single-lever memory
win measured, for almost no speed cost — because both scenarios do the
same total amount of compute, just organized into differently-shaped
chunks.

**Configurable**: `TrainConfig.grad_accum_steps`.

### 5. Memory-efficient optimizer configuration

**Problem**: AdamW keeps two extra full-size buffers per parameter
(`exp_avg`, `exp_avg_sq` — the first and second moment estimates), so
optimizer state alone is 2x the model's own parameter memory, on top of
gradients (1x) and the parameters themselves (1x) — 4x total, before any
activations.

**Fix**: `optimizer="sgd"` (with momentum) keeps only *one* state buffer
per parameter instead of two — `TrainConfig.optimizer`, wired through
`build_optimizer()`.

**Tradeoff**: SGD+momentum typically needs more careful learning-rate
tuning and more steps to reach the same loss AdamW would — it's a
well-known, real convergence-speed cost for the memory savings, not a
free lunch.

**Measured**: exactly 2x vs. 1x, proven directly by counting state tensor
elements after one real optimizer step (`test_adamw_keeps_twice_the_optimizer_state_sgd_does`)
— `state_numel(AdamW) == 2 * param_count`, `state_numel(SGD) == param_count`,
both exactly, since state is lazily allocated per-parameter and both
optimizers were stepped once on the same model. In the end-to-end
benchmark script, this effect gets swamped: **-0.1% peak RSS** (757.0MB →
756.4MB, noise) — a genuinely useful lesson in its own right about *when*
each lever actually matters. This benchmark's 5.4M-parameter model's
AdamW state is only ~41MB (2 x 5.4M x 4 bytes); its seq_len=256,
batch_size=8 activations (especially attention's per-layer
`O(seq_len^2)` score matrices) run into the hundreds of MB. Optimizer
choice matters most when parameter count is large *relative to*
activation size — bigger models, shorter sequences, larger models trained
on shorter contexts — not the long-sequence regime the other scenarios
here were tuned to highlight.

**Configurable**: `TrainConfig.optimizer` (`"adamw"` / `"sgd"`).

Every optimization above is proven correct (matches the unoptimized path,
or its exact expected memory multiplier) before any memory number is
trusted — the same "correctness before speed" discipline as Milestone 7's
KV cache.

## Scaling to 1B+ Parameters

**This project has never trained anything at `medium` or `xl_1b` scale.**
What it *can* do is tell you, instantly and without building anything,
exactly how big a config is and roughly what training it would cost:

```
python -m ashugpt.inspect_model --config 1b
python -m ashugpt.inspect_model --config small --batch-size 8 --seq-len 512 --optimizer sgd
python -m ashugpt.inspect_model --all              # every built-in preset, side by side
```

```
=== xl_1b ===  [ARCHITECTURE CONFIGURATION -- not a trained model; see explanation below]
Layers:                       22
Hidden dimension:             2048
Attention heads:              32
Vocabulary size:              50,304
Context length:               2048
Parameter count:             1,233,479,680
Weight memory (FP32):        4.934 GB
Weight memory (BF16):        2.467 GB
Gradient memory (FP32):      4.934 GB
Optimizer memory:            9.868 GB
Activation memory (est.):    7.151 GB
Estimated total (training):  26.887 GB
(activation estimate assumes batch_size=1, seq_len=2048, optimizer=adamw)
```

That last line is a real, if rough, number: ~16 bytes/param for
weights+gradients+AdamW state (1.23B × 16B ≈ 19.7GB) plus ~7GB of
estimated activation memory ≈ 26.7GB, which is what the tool actually
computes — consistent with the standard rule of thumb for full-precision
Adam training, computed from this project's own `ModelConfig` shape
formula rather than quoted from memory.

**How the estimate is built** (`ashugpt/utils/memory.py`), and why it's
honest about what it does and doesn't know about *this* project's
training loop specifically, not a generic textbook formula:

- **Weights** — `param_count x 4 bytes` (FP32) or `x 2 bytes` (BF16).
  Both are reported because they answer different questions: FP32 is
  what a checkpoint on disk / the actual training state costs; BF16 is
  what a bf16-native inference deployment would cost.
- **Gradients** and **optimizer state** are always estimated at FP32 —
  because that's what this project's training loop actually does.
  `TrainConfig.amp_dtype` only wraps the *forward pass* in `autocast`
  (Milestone 5); parameters themselves are never permanently cast to a
  lower dtype, so gradients and AdamW's `exp_avg`/`exp_avg_sq` inherit
  fp32 from the parameters regardless of `amp_dtype`. This is exactly
  the finding from the Memory Optimization section above (bf16 barely
  moved the needle on weight/optimizer memory in the real benchmark) —
  the estimator matches what was actually measured, not a generic
  mixed-precision-training assumption that wouldn't apply here.
- **Activations** are estimated at `bf16` by default (matching
  `TrainConfig`'s own default `amp_dtype`), dominated by each layer's
  `(batch, n_heads, seq_len, seq_len)` attention score matrix — the one
  genuinely quadratic term, and usually the largest single tensor once
  `seq_len` is more than a few hundred.
- Gradient checkpointing's effect on activation memory is deliberately
  **not** modeled symbolically here — Milestone 9 already *measured* it
  directly (-38.3% peak RSS), which is more trustworthy than re-deriving
  an estimate for something already proven empirically.
- **Calibration**: sanity-checked against Milestone 9's real measured RSS
  for a small training run — the estimator came in at roughly a third of
  the real number, with the gap traced to fixed Python/PyTorch process
  overhead (confirmed independently: importing torch and allocating one
  tensor alone costs ~190MB RSS in this environment) that doesn't scale
  with model size. So accuracy should *improve*, not worsen, at the
  GB-scale configs this tool exists for — that gap is single-digit
  percent of a 27GB estimate, not of a 750MB one.

Every preset — `tiny`, `small`, `medium`, `xl_1b` — reports in well under
a second, including `xl_1b`, because `estimate_memory()` only ever calls
`ModelConfig.approx_param_count()` (pure arithmetic, Milestone 1); nothing
here constructs an `nn.Module`. `test_estimate_memory_is_instant_even_for_a_billion_parameter_config`
asserts this directly (< 1 second).

### Implemented architecture vs. trained model vs. pretrained checkpoint

Three genuinely different things this project is careful never to
conflate, and which `python -m ashugpt.inspect_model`'s output spells out
in full every time it runs (not just for `xl_1b` — the same explanation
prints regardless of which config you inspect):

1. **Implemented architecture** — a `ModelConfig` this codebase *can*
   construct into a real `nn.Module`. Has a parameter count and a memory
   estimate. Every weight would be freshly random-initialized if actually
   built. This is all any `inspect_model` report describes, `xl_1b`
   included.
2. **Model trained from scratch** — an architecture that was *actually*
   trained: real gradient descent, real data, a real checkpoint file on
   disk (`ashugpt/training/checkpoint.py`, the gitignored `checkpoints/`
   directory — self-trained only). Every checkpoint this project can
   currently load was trained this way, at `tiny`/`small` scale, on the
   small demo corpora in `tests/fixtures/`. Nothing at `medium` or
   `xl_1b` scale has been trained — this CPU-only environment makes that
   impractical, and the Memory Optimization section above has the
   measured numbers (not assumptions) showing exactly why.
3. **Pretrained checkpoint loaded for inference** — public weights
   (e.g. GPT-2's) downloaded and mapped onto this architecture for
   *inference only*, never trained or fine-tuned by this project. This is
   SPEC.md milestone M13 (`pretrained_loader.py`), not yet built. Its
   checkpoints would live in the gitignored `pretrained/` directory, kept
   strictly separate from `checkpoints/` so a self-trained model is never
   mistaken for a downloaded one.

`xl_1b` is case 1, full stop. Every report says so explicitly.
