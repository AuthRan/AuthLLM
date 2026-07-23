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

    def __post_init__(self) -> None:
        if self.batch_size <= 0 or self.seq_len <= 0 or self.max_steps <= 0:
            raise ValueError("batch_size, seq_len, and max_steps must all be positive")
        if self.grad_accum_steps <= 0:
            raise ValueError("grad_accum_steps must be positive")
        if self.warmup_steps > self.max_steps:
            raise ValueError(f"warmup_steps ({self.warmup_steps}) must be <= max_steps ({self.max_steps})")
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
