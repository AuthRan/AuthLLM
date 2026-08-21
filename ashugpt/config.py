"""Configuration system for AshuGPT model architectures and training runs.

A ModelConfig fully describes the shape of a decoder-only transformer.
Presets for each size tier live as YAML files under configs/model/.

A TrainConfig fully describes one pretraining run's hyperparameters.
Presets live under configs/train/.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModelConfig:
    name: str
    vocab_size: int
    d_model: int
    n_layers: int
    n_heads: int
    n_kv_heads: int
    d_ff: int
    context_length: int
    rope_theta: float = 10000.0
    norm_eps: float = 1e-5
    tie_embeddings: bool = True

    def __post_init__(self) -> None:
        if self.vocab_size <= 0 or self.d_model <= 0 or self.n_layers <= 0:
            raise ValueError("vocab_size, d_model, and n_layers must all be positive")
        if self.d_model % self.n_heads != 0:
            raise ValueError(
                f"d_model ({self.d_model}) must be divisible by n_heads ({self.n_heads})"
            )
        if self.n_heads % self.n_kv_heads != 0:
            raise ValueError(
                f"n_heads ({self.n_heads}) must be divisible by n_kv_heads ({self.n_kv_heads})"
            )

    @property
    def head_dim(self) -> int:
        """Dimension of each attention head."""
        return self.d_model // self.n_heads

    def approx_param_count(self) -> int:
        """Estimate parameter count from shape alone, without building a model.

        Assumes tied input/output embeddings, no linear biases (matches the
        RMSNorm/SwiGLU/RoPE architecture, which conventionally omits biases),
        and a 3-matrix SwiGLU FFN (gate, up, down projections).
        """
        kv_dim = self.n_kv_heads * self.head_dim

        embedding = self.vocab_size * self.d_model
        attn_per_layer = 2 * self.d_model * self.d_model + 2 * self.d_model * kv_dim
        ffn_per_layer = 3 * self.d_model * self.d_ff
        norm_per_layer = 2 * self.d_model  # pre-attention + pre-FFN RMSNorm
        per_layer = attn_per_layer + ffn_per_layer + norm_per_layer
        final_norm = self.d_model
        head = 0 if self.tie_embeddings else self.vocab_size * self.d_model

        return embedding + self.n_layers * per_layer + final_norm + head


def load_model_config(path: str | Path) -> ModelConfig:
    """Load a ModelConfig from a YAML preset file."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return ModelConfig(**raw)


@dataclass
class TrainConfig:
    batch_size: int
    seq_len: int
    max_steps: int
    warmup_steps: int
    max_lr: float
    min_lr: float
    grad_accum_steps: int = 1
    weight_decay: float = 0.1
    betas: tuple[float, float] = (0.9, 0.95)
    grad_clip: float = 1.0
    log_interval: int = 10
    eval_interval: int = 100
    eval_steps: int = 20  # number of validation batches to average per eval
    checkpoint_interval: int = 200
    amp_dtype: str = "bfloat16"  # "bfloat16" | "float16" | "none" -- SPEC calls this "mixed_precision"
    seed: int = 1337

    # Memory optimizations (Milestone 9) -- see README.md's "Memory
    # Optimization" section for what each trades off and measured impact.
    gradient_checkpointing: bool = False
    use_efficient_attention: bool = False
    optimizer: str = "adamw"  # "adamw" | "sgd"

    # Sequence packing (instruction tuning only). Off by default: it changes
    # what one optimizer step contains, so an existing config's step count and
    # learning rate do not carry over unexamined -- see README.md section 10.6.
    pack_sequences: bool = False

    # Preference tuning only (scripts/preference_tune.py). How far the policy
    # is allowed to drift from the frozen reference: small beta permits more
    # divergence, large beta holds it close. It lives here rather than on the
    # command line because it changes what the run optimizes as surely as the
    # learning rate does, and a run should be reproducible from its config
    # file alone. See README.md section 10.8.
    dpo_beta: float = 0.1

    # Preference tuning only. Divides each sequence's log-probability by its
    # own supervised token count, so the objective ranks answers per token
    # rather than in total. It is a different algorithm, not a scaling
    # detail: summed log-probabilities punish an answer for being long, and
    # on this repo's preference data that effect is larger than any
    # difference in content -- see README.md section 10.8. Its beta does not
    # mean what the standard one's beta means, since the margins are ~L times
    # smaller, so a run under this flag is not comparable to one without it
    # except through the reference-free metrics.
    dpo_length_normalized: bool = False

    # Data windowing. None means "let the dataset decide" -- TokenizedDataset
    # defaults to 1 (every start position, maximally overlapping) and
    # ShardedTokenDataset to seq_len (disjoint). Set explicitly to override;
    # a real pretraining run wants stride == seq_len so one pass over the
    # dataset is one pass over the corpus.
    stride: int | None = None

    # Input pipeline. Irrelevant on CPU, where the model step dominates;
    # decisive on GPU, where a single-threaded loader will starve the
    # accelerator. num_workers=0 keeps loading in the training process
    # (the historical behavior).
    num_workers: int = 0
    pin_memory: bool = True

    # torch.compile the model before training. Off by default: it costs a
    # one-time graph compile of a minute or two, which is pure overhead for
    # the short runs the tests and demos do.
    compile_model: bool = False

    # How to use more than one GPU. "ddp" replicates the model on every rank
    # and all-reduces gradients; "fsdp" sharded parameters, gradients and
    # optimizer state across ranks so a model larger than one card can train
    # at all. Both are no-ops at world_size 1. See README.md section 12.
    parallel: str = "ddp"  # "ddp" | "fsdp"

    # FSDP only. Keeps the sharded parameters and optimizer state in host RAM
    # and streams each layer to the GPU as it is reached. This is what makes
    # xl_1b trainable on 2x11GB (2.04GB/GPU against an OOM without it) and it
    # costs roughly 2x the step time, so it is off unless a model needs it.
    fsdp_cpu_offload: bool = False

    def __post_init__(self) -> None:
        if self.parallel not in ("ddp", "fsdp"):
            raise ValueError(f'parallel must be "ddp" or "fsdp", got {self.parallel!r}')
        if self.fsdp_cpu_offload and self.parallel != "fsdp":
            raise ValueError("fsdp_cpu_offload only applies when parallel is 'fsdp'")
        if self.batch_size <= 0 or self.seq_len <= 0 or self.max_steps <= 0:
            raise ValueError("batch_size, seq_len, and max_steps must all be positive")
        if self.grad_accum_steps <= 0:
            raise ValueError("grad_accum_steps must be positive")
        if self.stride is not None and self.stride <= 0:
            raise ValueError(f"stride must be positive when set, got {self.stride}")
        if self.num_workers < 0:
            raise ValueError(f"num_workers must be >= 0, got {self.num_workers}")
        if self.warmup_steps > self.max_steps:
            raise ValueError(f"warmup_steps ({self.warmup_steps}) must be <= max_steps ({self.max_steps})")
        if self.dpo_beta <= 0:
            raise ValueError(f"dpo_beta must be positive, got {self.dpo_beta}")
        if self.optimizer not in ("adamw", "sgd"):
            raise ValueError(f"optimizer must be 'adamw' or 'sgd', got '{self.optimizer}'")


def load_train_config(path: str | Path) -> TrainConfig:
    """Load a TrainConfig from a YAML preset file."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    if "betas" in raw:
        raw["betas"] = tuple(raw["betas"])
    return TrainConfig(**raw)
