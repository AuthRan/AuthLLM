# Spec: AshuGPT — GPT-style Decoder-only LLM Built From Scratch

Status: **Implementation substantially complete** — see [README.md](README.md)
for the primary technical documentation (architecture, math, training,
inference, reproducibility, commands). This file is the original design
spec plus a living, honest milestone-by-milestone log of what was actually
built, where a plan changed after something was measured, and what
remains unbuilt (§"What's Not Built" in the README has the current
summary; the milestone table below has the full detail).

## Objective

Build a GPT-style decoder-only LLM entirely from first principles in PyTorch —
tokenizer, architecture, training loop, distributed/mixed-precision training,
checkpointing, evaluation, and inference — without relying on
`transformers.AutoModel`/`Trainer`. Primary goal is deep understanding of the
full stack; secondary goal is a credible, honestly-labeled resume artifact.

Audience for the code itself: an engineering student who wants to read every
line and understand *why*, not a black-box user. Prefer simple, explicit
PyTorch over configurable enterprise abstractions.

**Success looks like:** a tiny/small model trained by you from random init on
CPU that generates coherent short text, every architectural component
(RoPE, RMSNorm, SwiGLU, causal attention, KV cache) individually unit-tested
against known-correct behavior, and a config system that *also* describes a
1B+ parameter model correctly (shape-verified) even though you can't train it
on current hardware.

## Constraints (from clarification)

- **Compute: CPU only, no GPU currently.** Real training happens at
  tiny/small scale. Mixed precision, gradient checkpointing, and DDP are
  implemented for real and tested for correctness on CPU (bf16 autocast works
  on CPU; DDP is testable via multi-process `gloo` backend), but are not
  exercised at large scale until GPU access exists. 1B+ configs must be
  correct and shape-tested, not necessarily trainable today.
- **Tokenizer: hybrid.** From-scratch BPE trainer/encoder implemented for
  learning and tested, but `tiktoken`'s GPT-2 BPE is the tokenizer actually
  used for real training runs.
- **Dataset: TinyStories-style corpus** for early milestones — small, simple
  text where even a few-million-parameter model produces coherent output
  quickly, which validates the pipeline works end-to-end before scaling up.

## Tech Stack

- Python 3.11+, PyTorch (CPU build now, same code works under CUDA later)
- `tiktoken` (production tokenizer), `numpy` (memmap token shards), `PyYAML`
  (configs), `pytest` (tests), `tqdm` (progress)
- `safetensors` for checkpoint serialization (not raw pickle — avoids
  arbitrary code execution on load)
- Later milestones: `fastapi` + `uvicorn` (web interface),
  `transformers` used *only* as a weight-name reference when writing the
  optional public-checkpoint loader — never in the training/model path

## Commands

```
python -m venv .venv && .venv\Scripts\activate      # or source .venv/bin/activate
pip install -r requirements.txt

pytest                                                # all tests
pytest tests/unit                                     # fast, run constantly
pytest tests/integration                               # slower, pre-milestone

python scripts/train_tokenizer.py --config configs/data/tinystories.yaml
python scripts/prepare_data.py --config configs/data/tinystories.yaml
python scripts/train.py --model configs/model/tiny.yaml --train configs/train/tiny_cpu.yaml
python scripts/evaluate.py --checkpoint checkpoints/tiny/latest.safetensors
python scripts/generate.py --checkpoint checkpoints/tiny/latest.safetensors --prompt "Once upon a time"

torchrun --nproc_per_node=2 scripts/train.py --model configs/model/small.yaml --train configs/train/ddp_cpu_smoke.yaml
```

## Project Structure

```
authLLM/
├── SPEC.md                      # this document — living spec
├── README.md                    # what it is, what's self-trained vs loaded
├── requirements.txt
├── configs/
│   ├── model/                   # tiny.yaml, small.yaml, medium.yaml, xl_1b.yaml
│   ├── train/                   # tiny_cpu.yaml, ddp_cpu_smoke.yaml, cloud_gpu.yaml (future)
│   └── data/                    # tinystories.yaml
├── ashugpt/                     # importable package — all real logic lives here
│   ├── config.py                # ModelConfig/TrainConfig/DataConfig dataclasses + YAML load/merge
│   ├── tokenizer/
│   │   ├── base.py              # shared Tokenizer interface
│   │   ├── bpe_scratch.py       # from-scratch BPE trainer + encoder (learning)
│   │   └── bpe_tiktoken.py      # tiktoken GPT-2 BPE wrapper (production)
│   ├── model/
│   │   ├── norm.py              # RMSNorm
│   │   ├── rope.py              # RoPE positional embeddings
│   │   ├── attention.py         # causal self-attention + KV cache hook
│   │   ├── feedforward.py       # SwiGLU FFN
│   │   ├── block.py             # one decoder block (attn + ffn + norms + residuals)
│   │   └── gpt.py                # full decoder-only model, built from ModelConfig
│   ├── data/
│   │   ├── download.py           # fetch raw corpus
│   │   ├── preprocess.py         # clean, tokenize, pack into token shards
│   │   └── dataset.py            # memmap-backed Dataset/DataLoader
│   ├── training/
│   │   ├── trainer.py            # training loop orchestration
│   │   ├── optim.py              # optimizer + LR schedule factory
│   │   ├── amp.py                # mixed precision helpers
│   │   ├── ddp.py                # DDP setup/teardown
│   │   └── checkpoint.py         # save/load/resume (safetensors)
│   ├── eval/
│   │   └── perplexity.py
│   ├── inference/
│   │   ├── kv_cache.py
│   │   ├── generate.py           # temperature / top-k / top-p / greedy sampling
│   │   ├── engine.py             # clean high-level inference API
│   │   └── pretrained_loader.py  # OPTIONAL: map public GPT-2 weights into AshuGPT arch
│   └── utils/                    # logging, seeding, device helpers
├── scripts/                      # thin CLIs calling into ashugpt/
├── tests/
│   ├── unit/                     # one file per component in ashugpt/model, tokenizer, etc.
│   ├── integration/               # train-step, checkpoint-resume, generate end-to-end, DDP smoke
│   └── conftest.py
├── checkpoints/                  # gitignored — YOUR self-trained checkpoints only
├── pretrained/                   # gitignored — downloaded public weights, inference-only, clearly labeled
├── data/                         # gitignored — raw/processed corpora
└── webapp/                       # later milestone — FastAPI + minimal frontend
```

**Module responsibilities, one line each:**

| Module | Responsibility |
|---|---|
| `config.py` | Single source of truth for every hyperparameter; dataclasses + YAML, no magic numbers elsewhere |
| `tokenizer/base.py` | Interface both tokenizer impls satisfy, so trainer/inference code is tokenizer-agnostic |
| `tokenizer/bpe_scratch.py` | Hand-written BPE merge-learning + encode/decode, for understanding |
| `tokenizer/bpe_tiktoken.py` | Thin wrapper making tiktoken satisfy the same interface, used for real runs |
| `model/norm.py` | RMSNorm only |
| `model/rope.py` | Rotary position embedding application to Q/K |
| `model/attention.py` | Causal self-attention; accepts an optional KV cache object |
| `model/feedforward.py` | SwiGLU gated FFN |
| `model/block.py` | Wires norm→attn→residual→norm→ffn→residual |
| `model/gpt.py` | Embedding + N blocks + final norm + LM head, built entirely from `ModelConfig` |
| `data/preprocess.py` | Raw text → tokens → packed fixed-length sequences → on-disk shards |
| `data/dataset.py` | Reads shards via `numpy.memmap`, yields `(input_ids, target_ids)` |
| `training/trainer.py` | The actual loop: forward → loss → backward (accum) → step → log → periodic eval/checkpoint |
| `training/amp.py` | `torch.autocast` context (bf16, CPU or CUDA) |
| `training/ddp.py` | Wraps model in `DistributedDataParallel` when `world_size > 1` |
| `training/checkpoint.py` | Save/restore model+optimizer+scheduler+step, safetensors format |
| `eval/perplexity.py` | Held-out loss → perplexity |
| `inference/kv_cache.py` | Growing K/V buffer per layer, used during autoregressive generation |
| `inference/generate.py` | Sampling strategies: greedy, temperature, top-k, top-p |
| `inference/engine.py` | `AshuGPT.from_checkpoint(path).generate(prompt, ...)` — the clean public API |
| `inference/pretrained_loader.py` | Maps a public checkpoint's state dict onto AshuGPT's module names, inference-only |

## Configuration System

Plain `@dataclass` configs (not Hydra/OmegaConf — matches "simple, readable"
requirement), one YAML file per named scale under `configs/model/`:

```python
@dataclass
class ModelConfig:
    vocab_size: int
    d_model: int
    n_layers: int
    n_heads: int
    n_kv_heads: int          # supports future GQA; == n_heads for now
    d_ff: int                # SwiGLU hidden dim
    context_length: int
    rope_theta: float = 10000.0
    norm_eps: float = 1e-5
    tie_embeddings: bool = True

    def __post_init__(self):
        assert self.d_model % self.n_heads == 0
        assert self.n_heads % self.n_kv_heads == 0
```

`TrainConfig` and `DataConfig` follow the same pattern. A tiny `load_config(path, overrides=None)`
merges YAML → dataclass, with CLI `--set key=value` overrides for sweeps.
Validation happens in `__post_init__`, so a bad config fails at construction,
not three hours into a training run.

## Scaling Strategy — Tiny → 1B+

| Tier | d_model | layers | heads | ctx | ~params | Trained where |
|---|---|---|---|---|---|---|
| `tiny` | 128 | 4 | 4 | 256 | ~2–5M | CPU, seconds/step — pipeline validation + fast tests |
| `small` | 384 | 6 | 6 | 512 | ~25–30M | CPU, real TinyStories training run (the actual "I trained this" artifact) |
| `medium` | 768 | 12 | 12 | 1024 | ~125M | Shape/forward tested on CPU now; real training needs GPU |
| `xl_1b` | 1536–2048 | 24+ | 16+ | 2048 | 1B+ | Config + shape tests only until cloud GPU is in the picture |

Every tier is unit-tested for a correct forward pass and parameter count;
only `tiny` and `small` are expected to actually be trained to convergence on
current hardware. This keeps the "1B+ configurable" requirement honest: the
*architecture* supports it today, the *training run* doesn't yet.

**Milestone 10** added `ashugpt/utils/memory.py` + `python -m
ashugpt.inspect_model` specifically to make that distinction checkable,
not just asserted: exact parameter count and estimated FP32/BF16
weight/gradient/optimizer/activation memory for any preset, computed as
pure arithmetic on `ModelConfig` (no `nn.Module` ever built, so it's
instant even for `xl_1b`). Measured `xl_1b` estimate: 1,233,479,680 params,
4.93GB FP32 weights, 2.47GB BF16 weights, ~26.9GB estimated total training
memory with AdamW at batch_size=1/seq_len=2048 — consistent with the
well-known "~16 bytes/param for weights+grad+Adam state" rule of thumb
(1.23B × 16B ≈ 19.7GB, + ~7GB activations ≈ 26.7GB). The CLI's output
explicitly labels every report "ARCHITECTURE CONFIGURATION -- not a
trained model" and prints the implemented-architecture /
trained-from-scratch / pretrained-checkpoint distinction every time, so
this can never be read as a claim that `xl_1b` was actually trained.

## Training Pipeline

1. Load `ModelConfig` + `TrainConfig`, build model, optimizer (AdamW), LR
   schedule (warmup + cosine decay).
2. Build `DataLoader` over memmapped token shards.
3. Loop per micro-batch: `autocast(bf16)` forward → loss → `backward()`
   (accumulated over `grad_accum_steps`) → every N micro-batches: clip grad,
   `optimizer.step()`, `scheduler.step()`, `zero_grad()`.
4. Gradient checkpointing toggled per-block via config
   (`torch.utils.checkpoint.checkpoint`), independently testable — same loss
   trajectory with/without, given a fixed seed.
5. DDP: when launched via `torchrun`, wrap model, use `DistributedSampler`;
   validated on CPU today with 2-process `gloo` runs comparing gradients
   against a single-process baseline. Real NCCL/multi-GPU path is the same
   code, exercised later on GPU.
6. Periodic: eval-set perplexity, checkpoint save (safetensors + optimizer
   state + step count), stdout/CSV logging.

## Data Preprocessing Pipeline

`download.py` fetches TinyStories → `preprocess.py`: normalize whitespace,
tokenize with the tiktoken wrapper, concatenate documents with an EOS
separator, pack into fixed-length `context_length` blocks, write as
`uint16` token arrays to disk (nanoGPT-style `.bin` shards, memmap-friendly
for CPU, no need to hold the whole corpus in RAM). `dataset.py` memmaps the
shard and returns sliding-window `(input_ids, target_ids)` pairs.

## Testing Strategy

`pytest`, two tiers:

- **`tests/unit/`** — fast, run on every change. One file per component:
  RMSNorm output-norm property, RoPE rotation correctness (known-angle
  check), causal mask never attends to future positions, SwiGLU shapes,
  tokenizer encode/decode round-trip, config validation rejects bad shapes.
- **`tests/integration/`** — slower, run before closing a milestone: a real
  tiny-config train step where loss measurably decreases over N steps;
  checkpoint save→load→resume produces the identical subsequent loss curve
  given the same seed; **KV-cache vs no-cache greedy generation produce
  byte-identical output** (this is the correctness test that actually
  matters for #17); DDP 2-process CPU smoke test.

GPU-only tests are marked `@pytest.mark.skipif(not torch.cuda.is_available())`
so the suite stays green on CPU-only dev and simply skips until GPU access
exists.

## Build Ourselves vs. Acceptable Libraries

**Build ourselves** (the point of the project): model architecture
(attention, RoPE, RMSNorm, SwiGLU, blocks, full GPT), training loop, sampling
strategies, KV cache, checkpoint format, config system, from-scratch BPE.

**Acceptable libraries**: PyTorch itself, `tiktoken` (production tokenizer),
`numpy`, `PyYAML`, `pytest`, `safetensors`, `fastapi`/`uvicorn` (web
milestone only).

**Explicitly avoided in the core path**: `transformers.AutoModel`,
`transformers.Trainer`, `accelerate`, `peft` — using these would defeat the
purpose. `transformers` may be imported *only* inside
`pretrained_loader.py` as a reference for public checkpoint parameter names,
never for training or as the model class itself.

## Self-Trained vs. Public Pretrained Checkpoints

Hard separation, enforced by directory + README labeling, not just
convention:

- `checkpoints/` — **only** models trained by you, in AshuGPT's own
  `state_dict` layout. This is what you can honestly call "a model I
  trained from scratch."
- `pretrained/` — downloaded public weights (e.g. GPT-2), converted into
  AshuGPT's architecture by `pretrained_loader.py` **for inference
  demonstration only**. Never used as a training starting point, never
  fine-tuned, never presented as self-trained. `README.md` states this
  distinction explicitly next to any demo using a `pretrained/` checkpoint.

Both directories are gitignored — checkpoints don't belong in version
control regardless of origin.

## Code Style

Type-hinted, dataclass-driven, PEP8/black-formatted. Comments only for
non-obvious math (e.g. *why* RoPE's rotation matrix is constructed a
particular way) — not restating what the code already says. No docstring
essays; a one-line docstring if the function's purpose isn't obvious from
its name and signature.

## Boundaries

- **Always:** write a unit test alongside each new architectural component
  before wiring it into the trainer; keep every hyperparameter in a config
  file, never hardcoded; keep `checkpoints/` and `pretrained/` separated and
  labeled.
- **Ask first:** adding a new heavy dependency; downloading a multi-GB
  dataset; starting a training run expected to take hours; any change to
  what's tracked vs. gitignored.
- **Never:** present a `pretrained/` checkpoint as self-trained; commit
  checkpoints or raw/processed data to git; use `transformers.AutoModel` or
  `Trainer` in the model/training path.

## Milestone Roadmap (implementation order)

| # | Milestone | Verify |
|---|---|---|
| M0 | Scaffolding: repo, `git init`, `config.py`, directory skeleton, `requirements.txt` | `pytest` collects zero tests, no import errors |
| M1 | Tokenizer: from-scratch BPE + tiktoken wrapper behind shared interface | Round-trip encode/decode tests pass on both — **from-scratch half done; tiktoken wrapper + `base.py` interface deferred to when the training pipeline needs the production tokenizer, since only one implementation exists so far** |
| M2 | Core components: RMSNorm, RoPE, causal attention (no cache), SwiGLU | **Done.** Unit tests per component pass, incl. causal-masking and KV-cache-offset-compatibility tests beyond what was originally scoped |
| M3 | Assemble `block.py` + `gpt.py` from `ModelConfig` | **Done.** `AshuGPT.num_parameters()` matches `ModelConfig.approx_param_count()` exactly for `tiny`/`small` (built and tested directly); `medium`/`xl_1b` remain config-level-only (shape/param-count checked via `test_config.py`, not built as real models yet — too slow/heavy for the unit suite) |
| M4 | Data pipeline: download → preprocess → pack → memmap `Dataset` | **Partially done.** In-memory tokenize-then-window `TokenizedDataset` implemented and tested (no `download.py`/on-disk memmap shards yet — deferred until a real multi-GB corpus needs streaming, per SPEC's "do not optimize prematurely" boundary) |
| M5 | Training loop v1, single-process CPU, `tiny` config | **Done.** Integration test proves loss decreases substantially (tiny overfitting test, `tests/integration/test_train_step.py`) |
| M6 | AMP (bf16) + grad accumulation + grad checkpointing, each togglable | **Done.** AMP (bf16/fp16-ready via a uniformly-called, conditionally-disabled `GradScaler`), grad accumulation, and grad checkpointing (`torch.utils.checkpoint`, `use_reentrant=False`, DDP-safe) all implemented, all configurable via `TrainConfig`, all tested for exact-gradient equivalence on/off. Milestone 9 added two more configurable memory levers beyond SPEC's original M6 scope: an optional fused-attention path (`use_efficient_attention`) and optimizer choice (`optimizer: "adamw" \| "sgd"`) — see `scripts/benchmark_memory.py` and README.md's Memory Optimization section for measured impact of all five |
| M7 | Checkpointing (safetensors) + resume | **Done, format adjusted.** Uses `torch.save`/`torch.load(weights_only=True)` rather than literal safetensors — a resumable checkpoint bundles optimizer state + step count, which doesn't fit safetensors' pure-tensor format; safetensors remains the right choice for a future weights-only inference export. Resume verified to restore weights/optimizer/step exactly; exact-loss-curve-continuation not tested (not requested this round) |
| M8 | Evaluation: val loss + perplexity | **Done** (against the synthetic/tiny corpus; not yet run against real TinyStories, which isn't downloaded yet per M4) |
| M9 | Generation v1: greedy/temperature/top-k/top-p, no cache | **Done.** `ashugpt/inference/generate.py` + `python -m ashugpt.generate` CLI; distribution/shape/EOS/batching tests pass |
| M10 | KV cache, wired into generation | **Done.** The low-level mechanism (per-layer growing K/V via `torch.cat`, offset-aware causal mask) was actually already built in M2-M3, specifically so this milestone would only need to wire `generate()` to use it — no `inference/kv_cache.py` file was added, since the cache is just the `list[tuple[k,v]]` the model already returns/accepts, not a new abstraction. Cached vs. uncached greedy output is byte-identical (tested); logits differ by ~1e-7 (float32 op-order noise); real speedup measured at `tiny`-model scale (1.5-1.7x by 150 tokens, growing with length, as expected from removing O(n) redundant recomputation per step) |
| M11 | DDP wrapper, CPU 2-process smoke test | **Done.** `ashugpt/training/ddp.py` + `trainer.py` integration (rank-zero-only logging/checkpointing, `no_sync()`-aware grad accumulation, DistributedSampler with per-epoch reshuffling). 2-process CPU/gloo smoke test proves both ranks converge to bit-identical weights and match a single-process mathematical baseline exactly. **Environment note**: this Windows CPU-only torch build's `torchrun`/`torch.distributed.run` elastic launcher fails its own rendezvous (`TCPStore`/libuv issue, unrelated to this project's code); the smoke test launches processes manually with the same env vars torchrun would set, exercising identical `dist.init_process_group()` code. README documents plain `torchrun` as the expected command for a normal Linux/CUDA deployment, with the manual-launch fallback noted for anyone hitting the same Windows-wheel issue |
| M12 | Real end-to-end `small` training run on TinyStories to convergence | Manual read: generated text is coherent short stories |
| M13 | Optional public-checkpoint loader (inference-only) | **Done, with a different (and more honest) outcome than originally assumed.** The original success criterion ("loaded model reproduces expected reference logits") assumed direct compatibility would be achievable — it isn't, for GPT-2 (the comparison target chosen), and this was verified rather than asserted: two independent, fundamental architecture incompatibilities (RoPE vs. GPT-2's learned absolute position embeddings; AshuGPT's gated SwiGLU FFN vs. GPT-2's plain 2-matrix GELU MLP) mean no key-renaming/reshaping produces a working model. `ashugpt/inference/pretrained_loader.py` implements the real, tested, numerically-verified partial conversion (attention Q/K/V/O via `c_attn` splitting + `Conv1D`-transpose, embeddings, norms) and `load_gpt2_checkpoint()` **refuses to succeed by default** (`IncompatibleArchitectureError`, `strict=True`), reporting missing/unexpected keys via PyTorch's own `load_state_dict(strict=False)` accounting. All architectural facts verified live against `openai-community/gpt2`'s real `config.json` + safetensors header (metadata only, not the full ~548MB of weight data — irrelevant to an architecture-compatibility question) — see `scripts/demo_pretrained_loading.py` and README.md's Pretrained Checkpoints section. `inference/engine.py` (M14) remains deferred. |
| M14 | Clean inference API (`engine.py`) | **Partially done, in a different shape than planned.** `ashugpt/api/service.py`'s `InferenceService.load(checkpoint, tokenizer).generate(...)` is functionally the same one-liner idea M14 envisioned, but built as the FastAPI server's internal service layer (Milestone 12) rather than a standalone `ashugpt.model` classmethod (`AshuGPT.from_checkpoint(...)`) — no separate top-level `engine.py` exists. Reusable as-is for a future standalone Python API if one's wanted later. |
| M15 | Web interface (FastAPI + minimal frontend) | **Backend half done.** `ashugpt/api/` is the FastAPI half of M15 — `POST /generate`, `GET /health`, model loaded once at startup, layered validation/error handling, `python scripts/serve.py` to run it, real end-to-end HTTP round-trips tested (`tests/unit/test_api.py`) and manually verified against a live server. No frontend (browser UI) built yet — `curl`/`scripts/benchmark_server.py` are the current clients. |
| M16 (stretch, needs GPU) | Scale to `medium`/`xl_1b` on rented GPU, real fp16/bf16 CUDA AMP + NCCL DDP | Loss curve at scale behaves as expected; no code changes needed vs. CPU path |

## Success Criteria

- Every component in `ashugpt/model/` has a passing unit test proving
  correctness against a known property, not just "it runs."
- A `small`-tier model, trained by you from random init on CPU, generates
  recognizably coherent short text on TinyStories-style prompts.
- KV-cache and no-cache generation are provably identical.
- `ModelConfig` for `xl_1b` builds a real `nn.Module` with the expected
  parameter count and a correct forward pass shape — even though it isn't
  trained yet.
- `checkpoints/` (self-trained) and `pretrained/` (public, inference-only)
  are never conflated anywhere in code, docs, or README claims.
- README clearly states, for any generation demo, whether the weights
  behind it were trained by you or loaded from a public checkpoint.

## Open Questions

- Exact license for TinyStories redistribution / whether to fetch it fresh
  each time vs. commit a fixed processed shard reference (leaning: fetch +
  cache locally, gitignored, documented in README).
- Whether `n_kv_heads < n_heads` (GQA) is worth building now vs. deferring —
  currently deferred; config field exists but defaults to `n_heads`.
- Web interface framework choice (FastAPI+vanilla JS vs. Gradio/Streamlit)
  — deferred until M15, low cost to decide later.

---
*Original project brief (Prompt 0) preserved for reference — see git history / this file's first version.*
