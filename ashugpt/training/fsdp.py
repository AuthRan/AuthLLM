"""Fully Sharded Data Parallel: training a model too big for one GPU.

DDP (ashugpt/training/ddp.py) replicates. Every rank holds the whole model,
the whole gradient, and the whole optimizer state, and the only thing
crossing the wire is an all-reduce of gradients. That is the right trade
until the model stops fitting, and then it is no trade at all: two 11GB
cards running DDP can train exactly what one 11GB card can train.

FSDP *shards* instead. Parameters, gradients and optimizer state are each
split across ranks, and a rank materializes a full layer only for the
moment it is computing with it:

    forward, layer i:   all-gather layer i's parameters -> compute -> free
    backward, layer i:  all-gather again -> compute grads -> reduce-scatter
                        so each rank keeps only its shard of the gradient
    optimizer step:     each rank updates only its own shard

The memory arithmetic that motivates this, for `xl_1b` (1,233,479,680
parameters) in fp32 with AdamW:

    parameters     4.93 GB
    gradients      4.93 GB
    AdamW m and v  9.87 GB
    ------------------------
    total         19.73 GB   against 11.26 GB usable on one 2080 Ti

Sharded across 2 ranks that is 9.87 GB each, which still does not leave
room for activations -- and measured, it does not fit (`OOM`). Add CPU
offload, which keeps the sharded parameters and optimizer state in host
RAM and streams each layer to the GPU as it is needed, and the same model
trains in **2.04 GB per GPU**. Slowly: 10.1 s/step at batch 1 x seq 512,
against 4.9 s/step for a model a tenth the size under DDP. That is the
honest shape of this feature -- it does not make a 1.2B model fast here,
it makes it *possible* here.

Two API notes, both of which cost a real debugging session:

1. **This uses FSDP2 (`fully_shard`), not FSDP1
   (`FullyShardedDataParallel`).** FSDP1 refuses to initialize on CPU at
   all in torch 2.13 -- `_init_device_handle` raises before any wrapping
   happens -- which would have made the 2-process CPU parity test in
   tests/integration/test_fsdp.py impossible, and that test is the only
   proof this repo has that sharded training computes the same thing
   unsharded training does. FSDP2 also modifies the module *in place*
   rather than returning a wrapper, so there is no `.module` indirection
   and no `"module."` state-dict prefix to strip.

2. **CPU offload requires parameters to be materialized on CPU**, and
   leaves non-parameter buffers behind. AshuGPT's RoPE cos/sin caches are
   buffers, so they stay on the host while activations run on the GPU and
   the first matmul fails with "Expected all tensors to be on the same
   device". `wrap_model_for_fsdp` moves them explicitly.
"""

from __future__ import annotations

import torch
import torch.nn as nn
from torch.distributed.checkpoint.state_dict import (
    StateDictOptions,
    get_model_state_dict,
    get_optimizer_state_dict,
    set_model_state_dict,
    set_optimizer_state_dict,
)
from torch.distributed.fsdp import CPUOffloadPolicy, FSDPModule, MixedPrecisionPolicy, fully_shard

from ashugpt.model.block import TransformerBlock
from ashugpt.training.ddp import DistributedInfo


def is_fsdp_model(model: nn.Module) -> bool:
    """True if `model` has been sharded by `fully_shard`.

    FSDP2 mixes `FSDPModule` into the module's type in place, so this is a
    property of the object the training loop already holds -- there is no
    wrapper to look inside, unlike DDP."""
    return isinstance(model, FSDPModule)


def wrap_model_for_fsdp(
    model: nn.Module,
    info: DistributedInfo,
    cpu_offload: bool = False,
    param_dtype: torch.dtype | None = None,
) -> nn.Module:
    """Shards `model` across `info.world_size` ranks and returns it.

    Sharding is applied per `TransformerBlock` and then to the root. The
    granularity is what decides the memory/communication trade: sharding
    only the root would all-gather every parameter in the model at once,
    which defeats the point, while sharding every `nn.Linear` separately
    would issue a collective per matmul. One unit per decoder block is the
    standard choice, and it matches how the model is already written --
    a block is the repeated unit whose parameters are all needed at the
    same moment and never before it.

    `param_dtype` runs the gathered parameters in a lower precision
    (compute in fp16/bf16, keep the sharded master copy in fp32). This is
    FSDP's own mixed precision rather than `torch.autocast`, because the
    dtype has to be decided at all-gather time -- the point is to move
    half as many bytes over the wire, which autocast, running after the
    gather, cannot do.

    A world_size of 1 returns the model unchanged: there is nothing to
    shard it across, and this keeps single-GPU runs on exactly the code
    path they had before FSDP existed.
    """
    if not info.is_distributed:
        return model.to(info.device)

    kwargs: dict = {}
    if cpu_offload:
        kwargs["offload_policy"] = CPUOffloadPolicy()
    if param_dtype is not None:
        kwargs["mp_policy"] = MixedPrecisionPolicy(param_dtype=param_dtype, reduce_dtype=torch.float32)

    # CPU offload wants the sharded copy in host memory; everything else
    # wants it on the accelerator. Move before sharding either way, so
    # fully_shard sees parameters where it expects them.
    #
    # Unless the model was built on the meta device, which is how you
    # construct something that does not fit: meta parameters have shapes
    # and no storage, so sharding can decide who owns what before a single
    # byte is allocated. `.to()` cannot move them ("Cannot copy out of meta
    # tensor; no data"), and must not -- the caller materializes its own
    # shard with `to_empty()` after this returns, which is the difference
    # between allocating 4.9GB per rank and allocating 2.5GB.
    if not any(p.is_meta for p in model.parameters()):
        model = model.to("cpu" if cpu_offload else info.device)

    for block in model.blocks:
        fully_shard(block, **kwargs)
    fully_shard(model, **kwargs)

    if cpu_offload:
        place_buffers_on_device(model, info.device)

    return model


def place_buffers_on_device(model: nn.Module, device: torch.device | str) -> None:
    """Moves non-parameter buffers to the compute device.

    FSDP manages parameters, not buffers, so CPU offload streams a layer's
    weights to the GPU and leaves its buffers on the host. AshuGPT keeps
    RoPE's cos/sin caches as buffers, so without this the first attention
    layer fails with "Expected all tensors to be on the same device" --
    which reads like a bug in the model and is in fact a consequence of
    where offloading draws its line.

    Meta buffers are skipped: a model built on the meta device has no
    storage to move yet, and the caller materializes it with `to_empty()`
    *after* sharding (that ordering is the point -- see the note in
    `wrap_model_for_fsdp`). Call this again once that has happened.
    """
    for module in model.modules():
        for name, buffer in list(module.named_buffers(recurse=False)):
            if buffer.is_meta:
                continue
            module.register_buffer(name, buffer.to(device), persistent=False)


def fsdp_state_dicts(model: nn.Module, optimizer: torch.optim.Optimizer) -> tuple[dict, dict]:
    """Gathers full, unsharded, plain-tensor state dicts on every rank.

    **This is a collective.** Every rank must call it, or the ones that do
    will wait forever for shards from the ones that did not -- which is
    why the training loop cannot keep its "save on rank 0 only" shape
    under FSDP without first gathering here.

    The output is deliberately identical in form to what a single-process
    run saves: same keys, no `"module."` prefix, ordinary `torch.Tensor`
    values rather than `DTensor` shards, on CPU. That is what keeps
    `load_model_for_inference` -- which loads with `weights_only=True` and
    knows nothing about distributed training -- able to read a checkpoint
    written by a 2-GPU sharded run.
    """
    options = StateDictOptions(full_state_dict=True, cpu_offload=True)
    model_state = get_model_state_dict(model, options=options)
    optim_state = get_optimizer_state_dict(model, optimizer, options=options)
    return model_state, optim_state


def load_fsdp_state_dicts(
    model: nn.Module,
    optimizer: torch.optim.Optimizer | None,
    model_state: dict,
    optim_state: dict | None,
) -> None:
    """Loads a full (unsharded) checkpoint into a sharded model.

    Also a collective, and also the inverse of the property above: a
    checkpoint written by a single-process run loads into a sharded one,
    so a run can change its parallelism between restarts without its
    checkpoints becoming a different format.
    """
    options = StateDictOptions(full_state_dict=True, broadcast_from_rank0=True)
    set_model_state_dict(model, model_state_dict=model_state, options=options)
    if optimizer is not None and optim_state is not None:
        set_optimizer_state_dict(model, optimizer, optim_state_dict=optim_state, options=options)
