"""Memory estimation for a ModelConfig, without building an actual model.

Purpose: sanity-check whether a config -- especially a 1B+ one this
project has no intention of actually training locally (CPU-only, see
README.md's Memory Optimization section for exactly why bf16 doesn't save
the day here) -- could plausibly fit in a given amount of GPU memory, and
see which component (weights? optimizer state? activations?) would
dominate at a given scale. Works purely from ModelConfig's
approx_param_count() shape formula (Milestone 1), so it's instant even for
a billion-parameter config: nothing here builds an nn.Module.

These are order-of-magnitude estimates, not exact accounting -- real
memory also depends on the framework's allocator, fragmentation, workspace
buffers individual kernels need, Python/PyTorch's own fixed process
overhead, etc. Sanity-checked against Milestone 9's *actual measured* RSS
for a real (small) training run: this estimator came in at roughly a
third of the real number, with the gap dominated by fixed process/library
overhead that doesn't scale with model size -- so accuracy should improve,
not worsen, at the GB-scale configs this tool exists for. What's counted,
and what's deliberately left out, is documented on each function.
"""

from __future__ import annotations

from dataclasses import dataclass

from ashugpt.config import ModelConfig

BYTES_PER_ELEMENT = {"fp32": 4, "bf16": 2, "fp16": 2}


def weight_memory_bytes(param_count: int, dtype: str = "fp32") -> int:
    """Memory to store every parameter once, at the given dtype -- the
    relevant number for "how big is this checkpoint" or "what does this
    cost to just load for inference", not training."""
    return param_count * BYTES_PER_ELEMENT[dtype]


def gradient_memory_bytes(param_count: int) -> int:
    """This project's gradients always match each parameter's own dtype
    (fp32 -- parameters are never permanently downcast; autocast only
    changes the forward/backward *compute* dtype transiently, not what
    gets stored). Always fp32 here regardless of amp_dtype."""
    return param_count * BYTES_PER_ELEMENT["fp32"]


def optimizer_state_memory_bytes(param_count: int, optimizer: str = "adamw") -> int:
    """AdamW keeps two fp32 buffers per parameter (exp_avg, exp_avg_sq --
    see ashugpt/training/optim.py); SGD+momentum keeps one
    (momentum_buffer). Always fp32, for the same reason gradients are:
    this project's optimizers never downcast state."""
    if optimizer == "adamw":
        return 2 * param_count * BYTES_PER_ELEMENT["fp32"]
    if optimizer == "sgd":
        return 1 * param_count * BYTES_PER_ELEMENT["fp32"]
    raise ValueError(f"optimizer must be 'adamw' or 'sgd', got '{optimizer}'")


def activation_memory_bytes(config: ModelConfig, batch_size: int, seq_len: int, dtype: str = "bf16") -> int:
    """Rough estimate of the activation memory a training forward pass
    keeps alive for backward, dominated by two things per layer: the
    causal attention score matrix, shape (batch, n_heads, seq_len,
    seq_len) -- quadratic in seq_len, usually the single largest tensor by
    far once seq_len is more than a few hundred -- and the SwiGLU FFN's
    (batch, seq_len, d_ff) intermediate. A fixed multiplier of
    (batch, seq_len, d_model)-shaped tensors (Q/K/V/O projections, norm
    outputs, residual branches) covers everything else, rather than
    tracing the exact tensor count.

    Deliberately NOT modeled: gradient checkpointing's effect (see
    scripts/benchmark_memory.py for a *measured*, not estimated, number
    for that specific lever -- it depends on recompute granularity in a
    way that isn't worth re-deriving symbolically here), KV-cache memory
    (an inference-time cost, not a training one), or
    allocator/framework overhead.
    """
    bytes_per_element = BYTES_PER_ELEMENT[dtype]
    attn_scores = batch_size * config.n_heads * seq_len * seq_len
    ffn_intermediate = batch_size * seq_len * config.d_ff
    other_activations = 4 * batch_size * seq_len * config.d_model  # Q/K/V/O-ish tensors, per layer
    per_layer_elements = attn_scores + ffn_intermediate + other_activations
    return config.n_layers * per_layer_elements * bytes_per_element


@dataclass
class MemoryEstimate:
    param_count: int
    weight_memory_fp32: int
    weight_memory_bf16: int
    gradient_memory: int
    optimizer_memory: int
    activation_memory: int
    total_training_memory: int  # fp32 weights + fp32 grad + fp32 optimizer state + activations (at activation_dtype)

    def summary_lines(self) -> list[str]:
        def gb(n: int) -> str:
            return f"{n / 1e9:.3f} GB"

        return [
            f"Parameter count:             {self.param_count:,}",
            f"Weight memory (FP32):        {gb(self.weight_memory_fp32)}",
            f"Weight memory (BF16):        {gb(self.weight_memory_bf16)}",
            f"Gradient memory (FP32):      {gb(self.gradient_memory)}",
            f"Optimizer memory:            {gb(self.optimizer_memory)}",
            f"Activation memory (est.):    {gb(self.activation_memory)}",
            f"Estimated total (training):  {gb(self.total_training_memory)}",
        ]


def estimate_memory(
    config: ModelConfig,
    batch_size: int = 1,
    seq_len: int | None = None,
    optimizer: str = "adamw",
    activation_dtype: str = "bf16",
) -> MemoryEstimate:
    """seq_len defaults to config.context_length (the worst case for that
    config) if not given. activation_dtype defaults to "bf16" to match
    TrainConfig's own default amp_dtype -- change it to "fp32" to estimate
    training with mixed precision off entirely."""
    seq_len = config.context_length if seq_len is None else seq_len
    param_count = config.approx_param_count()

    weight_fp32 = weight_memory_bytes(param_count, "fp32")
    weight_bf16 = weight_memory_bytes(param_count, "bf16")
    grad_mem = gradient_memory_bytes(param_count)
    opt_mem = optimizer_state_memory_bytes(param_count, optimizer)
    act_mem = activation_memory_bytes(config, batch_size, seq_len, activation_dtype)

    # Weights/gradients/optimizer state stay FP32 in this project's actual
    # training loop regardless of amp_dtype (see the module docstrings
    # above) -- only activations genuinely shrink under mixed precision.
    total = weight_fp32 + grad_mem + opt_mem + act_mem

    return MemoryEstimate(
        param_count=param_count,
        weight_memory_fp32=weight_fp32,
        weight_memory_bf16=weight_bf16,
        gradient_memory=grad_mem,
        optimizer_memory=opt_mem,
        activation_memory=act_mem,
        total_training_memory=total,
    )
