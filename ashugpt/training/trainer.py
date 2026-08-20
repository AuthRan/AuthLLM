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
from ashugpt.data.batch import split_batch
from ashugpt.eval.perplexity import evaluate
from ashugpt.training.amp import (
    autocast_context,
    build_grad_scaler,
    resolve_amp_dtype,
    warn_if_amp_dtype_is_slow,
)
from ashugpt.training.checkpoint import load_checkpoint, save_checkpoint
from ashugpt.training.ddp import cleanup_distributed, setup_distributed, unwrap_model, wrap_model_for_ddp
from ashugpt.training.fsdp import (
    fsdp_state_dicts,
    is_fsdp_model,
    load_fsdp_state_dicts,
    wrap_model_for_fsdp,
)
from ashugpt.training.optim import build_optimizer, get_lr

_CSV_FIELDNAMES = ["step", "train_loss", "lr", "val_loss", "val_perplexity"]


def _append_csv_row(path: Path, row: dict) -> None:
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _truncate_csv_after(path: Path, start_step: int) -> int:
    """Drop logged rows for steps the resume is about to replay.

    Resuming rolls the model back to the last checkpoint, so any rows logged
    between that checkpoint and the crash describe work that no longer
    happened -- and the replayed steps log again under the same step numbers.
    Left alone the CSV accumulates duplicate, conflicting rows per step (this
    run already carries three rows for step 520 from two early restarts).
    Harmless at log_interval=20; at log_interval=1 a single crash duplicates a
    whole checkpoint interval. Rows *at* start_step are kept: they were logged
    before the checkpoint was written, so the checkpoint includes them.

    Returns the number of rows dropped.
    """
    if not path.exists():
        return 0

    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    kept = []
    for row in rows:
        try:
            row_step = int(row["step"])
        except (KeyError, TypeError, ValueError):
            kept.append(row)  # unparseable: keep rather than silently discard
            continue
        if row_step <= start_step:
            kept.append(row)

    dropped = len(rows) - len(kept)
    if dropped == 0:
        return 0

    # Write via a temp file + atomic replace: a crash mid-rewrite must not
    # leave a half-written history behind.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(kept)
    tmp_path.replace(path)
    return dropped


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
        if config.parallel == "fsdp":
            model = wrap_model_for_fsdp(model, info, cpu_offload=config.fsdp_cpu_offload)
        else:
            model = wrap_model_for_ddp(model, info)
        # FSDP2 shards in place, so this is the same object; DDP returns a
        # wrapper whose state_dict keys carry a "module." prefix.
        raw_model = unwrap_model(model)
        raw_model.set_memory_optimizations(
            gradient_checkpointing=config.gradient_checkpointing,
            efficient_attention=config.use_efficient_attention,
        )

        # Compile last, and keep `raw_model` pointing at the original module
        # captured above. torch.compile returns an OptimizedModule whose
        # state_dict keys are prefixed with "_orig_mod." -- checkpointing
        # through it would write files that plain inference cannot load. Every
        # checkpoint/eval path below already goes through raw_model, so
        # compiling only the object the training loop calls keeps the on-disk
        # format identical whether or not compilation was enabled.
        if config.compile_model:
            if info.is_main_process:
                print("Compiling model (one-time cost, typically 1-2 min)...")
            model = torch.compile(model)

        # pin_memory only means anything when copying to a GPU: it stages
        # batches in page-locked host memory so the H2D copy can be async
        # (the non_blocking=True below). On CPU it is wasted work.
        use_pinned = config.pin_memory and info.device.type == "cuda"
        loader_kwargs = {
            "batch_size": config.batch_size,
            "num_workers": config.num_workers,
            "pin_memory": use_pinned,
            # Workers are forked once and kept, rather than respawned every
            # epoch. This matters here because each worker re-opens the
            # dataset's memmaps on startup, and the training loop restarts the
            # iterator every time it exhausts the loader.
            "persistent_workers": config.num_workers > 0,
            "prefetch_factor": 4 if config.num_workers > 0 else None,
        }

        if info.is_distributed:
            train_sampler = DistributedSampler(
                train_dataset, num_replicas=info.world_size, rank=info.rank, shuffle=True, seed=config.seed
            )
            train_loader = DataLoader(train_dataset, sampler=train_sampler, **loader_kwargs)
        else:
            train_sampler = None
            train_loader = DataLoader(train_dataset, shuffle=True, **loader_kwargs)

        # Validation stays single-process (rank 0 only, over the whole
        # val_dataset, un-sharded) -- simpler than all-reducing a
        # distributed validation loss, and cheap enough at this scale that
        # it doesn't need to be split across ranks.
        #
        # Except under FSDP, where a forward pass is a collective: every
        # layer all-gathers its parameters from every rank. A rank-0-only
        # validation would leave rank 0 waiting for shards from ranks that
        # went straight on to the next training step, and the run would
        # hang with no error. So under FSDP every rank runs the same
        # validation over the same data -- redundant arithmetic, but it is
        # the same number on every rank, and only rank 0 logs it.
        run_validation_here = info.is_main_process or config.parallel == "fsdp"
        val_loader = None
        if val_dataset is not None and run_validation_here:
            val_loader = DataLoader(
                val_dataset,
                batch_size=config.batch_size,
                shuffle=False,
                num_workers=config.num_workers,
                pin_memory=use_pinned,
                persistent_workers=config.num_workers > 0,
                prefetch_factor=4 if config.num_workers > 0 else None,
            )

        optimizer = build_optimizer(
            model, lr=config.max_lr, weight_decay=config.weight_decay, betas=config.betas, optimizer=config.optimizer
        )
        amp_dtype = resolve_amp_dtype(config.amp_dtype)
        if info.is_main_process:
            amp_warning = warn_if_amp_dtype_is_slow(info.device.type, amp_dtype)
            if amp_warning is not None:
                print(f"WARNING: {amp_warning}")
        scaler = build_grad_scaler(info.device.type, amp_dtype)

        start_step = 0
        if resume_from is not None:
            # Every rank loads independently from the same checkpoint file
            # on shared/local disk -- simplest correct approach for
            # single-node multi-GPU; a non-shared-storage multi-node setup
            # would instead need rank 0 to load and broadcast.
            if is_fsdp_model(model):
                checkpoint = torch.load(resume_from, map_location="cpu", weights_only=True)
                load_fsdp_state_dicts(
                    model, optimizer, checkpoint["model_state_dict"], checkpoint["optimizer_state_dict"]
                )
                start_step = checkpoint["step"]
            else:
                start_step = load_checkpoint(resume_from, raw_model, optimizer)
            if info.is_main_process:
                print(f"Resumed from {resume_from} at step {start_step}")

        torch.manual_seed(config.seed)
        model.train()

        checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir is not None else None
        log_path = Path(log_path) if log_path is not None else None

        # Discard log rows for steps this resume is about to replay, so the CSV
        # stays one row per step per metric. Rank 0 only -- it owns the log.
        if log_path is not None and resume_from is not None and info.is_main_process:
            dropped = _truncate_csv_after(log_path, start_step)
            if dropped:
                print(f"Dropped {dropped} log row(s) after step {start_step} from {log_path}")

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
                    batch = next(train_iter)
                except StopIteration:  # ran out of batches -- start another pass over the data
                    epoch += 1
                    if train_sampler is not None:
                        train_sampler.set_epoch(epoch)  # reshuffle differently each epoch, same way on every rank
                    train_iter = iter(train_loader)
                    batch = next(train_iter)
                # non_blocking pairs with the loader's pinned memory: the H2D
                # copy is queued and the CPU runs ahead to enqueue the forward
                # pass instead of blocking on the transfer. It is a no-op
                # without pinned source memory, so it is safe unconditionally.
                # extras is empty unless the loader is packed, in which case it
                # carries segment_ids/position_ids -- see ashugpt/data/batch.py.
                input_ids, labels, extras = split_batch(batch, info.device, non_blocking=use_pinned)

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
                        output = model(input_ids, labels=labels, **extras)
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
            # The `or step == config.max_steps` clause matters whenever
            # max_steps is not a multiple of checkpoint_interval: without it a
            # run logs a final loss and a final validation and then throws away
            # the weights that produced them. The Alpaca fine-tune (max_steps
            # 4,875, checkpoint_interval 500) lost its final model exactly that
            # way -- the newest file on disk was step_4500.pt. Logging and
            # validation already special-case the last step; saving now does too.
            if checkpoint_dir is not None and (
                step % config.checkpoint_interval == 0 or step == config.max_steps
            ):
                ckpt_path = checkpoint_dir / f"step_{step}.pt"
                if is_fsdp_model(model):
                    # Gathering a full state dict is a collective too, so it
                    # happens on every rank and only the write is rank 0's.
                    # What lands on disk is byte-identical in *form* to a
                    # single-process checkpoint -- plain tensors, no shards,
                    # no rank count baked in -- so inference never learns
                    # that the run that produced it was sharded.
                    model_state, optim_state = fsdp_state_dicts(model, optimizer)
                    if info.is_main_process:
                        save_checkpoint(
                            ckpt_path,
                            raw_model,
                            optimizer,
                            step,
                            model_config,
                            config,
                            model_state=model_state,
                            optimizer_state=optim_state,
                        )
                elif info.is_main_process:
                    save_checkpoint(ckpt_path, raw_model, optimizer, step, model_config, config)
                if info.is_main_process:
                    print(f"Saved checkpoint to {ckpt_path}")

        return history
    finally:
        cleanup_distributed(info)
