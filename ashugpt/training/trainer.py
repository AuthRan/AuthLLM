"""The pretraining loop: forward -> loss -> backward -> (accumulate) ->
clip -> optimizer step -> scheduler step -> log -> (periodic) eval/checkpoint.

Transparently single-process or multi-process (DDP): `setup_distributed()`
returns a world_size=1 DistributedInfo when not launched via torchrun, so
every "if info.is_distributed" branch below is simply skipped and the loop
behaves exactly as it did before DDP existed. See ashugpt/training/ddp.py
for the setup/wrap/cleanup mechanics, and the module-level walkthrough in
README.md for what happens across ranks during one training step.
"""

from __future__ import annotations

import csv
from contextlib import nullcontext
from pathlib import Path

import torch
import torch.nn as nn
from torch.nn.parallel import DistributedDataParallel
from torch.utils.data import DataLoader, Dataset
from torch.utils.data.distributed import DistributedSampler

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.eval.perplexity import evaluate
from ashugpt.training.amp import autocast_context, build_grad_scaler, resolve_amp_dtype
from ashugpt.training.checkpoint import load_checkpoint, save_checkpoint
from ashugpt.training.ddp import cleanup_distributed, setup_distributed, unwrap_model, wrap_model_for_ddp
from ashugpt.training.optim import build_optimizer, get_lr

_CSV_FIELDNAMES = ["step", "train_loss", "lr", "val_loss", "val_perplexity"]


def _append_csv_row(path: Path, row: dict) -> None:
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def train(
    model: nn.Module,
    train_dataset: Dataset,
    val_dataset: Dataset | None,
    config: TrainConfig,
    model_config: ModelConfig | None = None,
    checkpoint_dir: str | Path | None = None,
    resume_from: str | Path | None = None,
    log_path: str | Path | None = None,
) -> list[dict]:
    """Runs up to config.max_steps optimizer steps.

    Takes Datasets rather than pre-built DataLoaders because the training
    DataLoader's sampler has to be built *after* rank/world_size are known
    (a DistributedSampler needs both to decide which shard of the data
    this process owns) -- that can't be decided by a caller that doesn't
    yet know whether it's about to run under torchrun.

    Returns `history` (only meaningful on rank 0 -- other ranks return an
    empty list, since nothing consumes their return value in a torchrun
    launch): a list of per-event dicts, each tagged with `step`.
    Logged-training-loss rows have `train_loss`/`lr`; validation rows have
    `val_loss`/`val_perplexity`.
    """
    if checkpoint_dir is not None and model_config is None:
        raise ValueError("model_config is required when checkpoint_dir is given, so checkpoints are self-describing")

    info = setup_distributed()
    try:
        model = wrap_model_for_ddp(model, info)
        raw_model = unwrap_model(model)  # for state_dict / anything DDP won't forward attribute access to
        raw_model.set_memory_optimizations(
            gradient_checkpointing=config.gradient_checkpointing,
            efficient_attention=config.use_efficient_attention,
        )

        if info.is_distributed:
            train_sampler = DistributedSampler(
                train_dataset, num_replicas=info.world_size, rank=info.rank, shuffle=True, seed=config.seed
            )
            train_loader = DataLoader(train_dataset, batch_size=config.batch_size, sampler=train_sampler)
        else:
            train_sampler = None
            train_loader = DataLoader(train_dataset, batch_size=config.batch_size, shuffle=True)

        # Validation stays single-process (rank 0 only, over the whole
        # val_dataset, un-sharded) -- simpler than all-reducing a
        # distributed validation loss, and cheap enough at this scale that
        # it doesn't need to be split across ranks.
        val_loader = None
        if val_dataset is not None and info.is_main_process:
            val_loader = DataLoader(val_dataset, batch_size=config.batch_size, shuffle=False)

        optimizer = build_optimizer(
            model, lr=config.max_lr, weight_decay=config.weight_decay, betas=config.betas, optimizer=config.optimizer
        )
        amp_dtype = resolve_amp_dtype(config.amp_dtype)
        scaler = build_grad_scaler(info.device.type, amp_dtype)

        start_step = 0
        if resume_from is not None:
            # Every rank loads independently from the same checkpoint file
            # on shared/local disk -- simplest correct approach for
            # single-node multi-GPU; a non-shared-storage multi-node setup
            # would instead need rank 0 to load and broadcast.
            start_step = load_checkpoint(resume_from, raw_model, optimizer)
            if info.is_main_process:
                print(f"Resumed from {resume_from} at step {start_step}")

        torch.manual_seed(config.seed)
        model.train()

        checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir is not None else None
        log_path = Path(log_path) if log_path is not None else None

        history: list[dict] = []
        epoch = 0
        if train_sampler is not None:
            train_sampler.set_epoch(epoch)
        train_iter = iter(train_loader)
        step = start_step

        while step < config.max_steps:
            # ---- scheduler step: this step's learning rate (warmup + cosine decay) ----
            lr = get_lr(step, config)
            for group in optimizer.param_groups:
                group["lr"] = lr

            # ---- gradient reset: start a fresh accumulation window ----
            optimizer.zero_grad(set_to_none=True)

            accumulated_loss = 0.0
            for micro_step in range(config.grad_accum_steps):
                try:
                    input_ids, labels = next(train_iter)
                except StopIteration:  # ran out of batches -- start another pass over the data
                    epoch += 1
                    if train_sampler is not None:
                        train_sampler.set_epoch(epoch)  # reshuffle differently each epoch, same way on every rank
                    train_iter = iter(train_loader)
                    input_ids, labels = next(train_iter)
                input_ids, labels = input_ids.to(info.device), labels.to(info.device)

                # DDP all-reduces gradients on every backward() by default --
                # correct but wasteful mid-accumulation-window, since only the
                # *sum* over the window matters. no_sync() defers the
                # all-reduce; the final backward() (outside no_sync) still
                # accumulates local grads correctly and triggers exactly one
                # all-reduce for the whole window.
                is_last_micro_step = micro_step == config.grad_accum_steps - 1
                sync_context = (
                    nullcontext()
                    if (is_last_micro_step or not isinstance(model, DistributedDataParallel))
                    else model.no_sync()
                )
                with sync_context:
                    # ---- forward pass + loss calculation ----
                    with autocast_context(info.device.type, amp_dtype):
                        output = model(input_ids, labels=labels)
                        loss = output.loss / config.grad_accum_steps  # average across the accumulation window

                    # ---- backward pass, with gradient scaling if using mixed precision ----
                    scaler.scale(loss).backward()
                accumulated_loss += loss.item()

            # ---- gradient clipping: unscale first, so clipping sees true-magnitude gradients ----
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)

            # ---- optimizer step (every rank applies the same synchronized gradient) ----
            scaler.step(optimizer)
            scaler.update()

            step += 1

            # ---- loss logging (rank 0 only) ----
            if info.is_main_process and (step % config.log_interval == 0 or step == config.max_steps):
                row = {"step": step, "train_loss": accumulated_loss, "lr": lr}
                print(f"step {step:6d} | train_loss {accumulated_loss:.4f} | lr {lr:.2e}")
                history.append(row)
                if log_path is not None:
                    _append_csv_row(log_path, row)

            # ---- validation loop (rank 0 only, plain forward on the raw model) ----
            if val_loader is not None and (step % config.eval_interval == 0 or step == config.max_steps):
                metrics = evaluate(raw_model, val_loader, amp_dtype, max_batches=config.eval_steps)
                model.train()  # evaluate() leaves the model in eval() mode; resume training explicitly
                print(f"step {step:6d} | val_loss {metrics['loss']:.4f} | val_ppl {metrics['perplexity']:.2f}")
                val_row = {"step": step, "val_loss": metrics["loss"], "val_perplexity": metrics["perplexity"]}
                history.append(val_row)
                if log_path is not None:
                    _append_csv_row(log_path, val_row)

            # ---- checkpoint saving (rank 0 only, always the unwrapped/plain model) ----
            if info.is_main_process and checkpoint_dir is not None and step % config.checkpoint_interval == 0:
                ckpt_path = checkpoint_dir / f"step_{step}.pt"
                save_checkpoint(ckpt_path, raw_model, optimizer, step, model_config, config)
                print(f"Saved checkpoint to {ckpt_path}")

        return history
    finally:
        cleanup_distributed(info)
