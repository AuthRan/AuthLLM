"""Integration test: real FSDP training across two actual OS processes
(gloo backend, CPU).

The claim sharding makes is that it changes *where* parameters, gradients
and optimizer state live, not what the training step computes. That is a
testable claim, and this is the test: the same scenario as
tests/integration/test_ddp.py, so both parallelism strategies are checked
against the same single-process baseline, which -- because the 8 examples
split evenly into 4 per rank, and mean(mean_A, mean_B) == mean(A union B)
for equal groups -- is exact algebra rather than an approximation.

Sharded training has three failure modes that a loss curve cannot show,
and each has a test here:

1. The optimizer updates only its own shard, so if the gather is wrong the
   ranks silently diverge and each holds a different model.
2. A checkpoint written from sharded state is full of `DTensor` shards
   unless it is gathered first, which produces a file that trains fine and
   cannot be loaded by plain single-process inference.
3. Anything collective that only some ranks call -- a rank-0-only
   validation forward, a rank-0-only state-dict gather -- hangs the run
   with no error at all rather than failing.

Processes are launched manually with the env vars torchrun would set, for
the same reason test_ddp.py does it: it exercises identical
`dist.init_process_group()` code without depending on the elastic
launcher.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from ashugpt.model.gpt import AshuGPT
from ashugpt.training.trainer import train

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fsdp_worker  # noqa: E402

WORKER_SCRIPT = Path(__file__).resolve().parent / "_fsdp_worker.py"
WORLD_SIZE = 2
TIMEOUT_SECONDS = 180


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_fsdp_worker(output_dir: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    port = _free_port()
    procs = []
    for rank in range(WORLD_SIZE):
        env = dict(os.environ)
        env.update(
            {
                "RANK": str(rank),
                "LOCAL_RANK": str(rank),
                "WORLD_SIZE": str(WORLD_SIZE),
                "MASTER_ADDR": "127.0.0.1",
                "MASTER_PORT": str(port),
                # Hidden deliberately: FSDP picks its compute device from
                # what is visible, and a gloo process group with CUDA in
                # sight segfaults rather than falling back to CPU. Making
                # that explicit here keeps the test meaningful on a machine
                # that happens to have GPUs.
                "CUDA_VISIBLE_DEVICES": "",
            }
        )
        procs.append(
            subprocess.Popen(
                [sys.executable, str(WORKER_SCRIPT), "--output-dir", str(output_dir), *(extra_args or [])],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    outs, errs, codes = [], [], []
    for p in procs:
        try:
            out, err = p.communicate(timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
            outs.append(out)
            errs.append(err + "\nTIMED OUT -- a collective was probably called on only some ranks")
            codes.append(1)
            continue
        outs.append(out)
        errs.append(err)
        codes.append(p.returncode)

    return subprocess.CompletedProcess(
        args=[str(WORKER_SCRIPT)], returncode=max(codes), stdout="".join(outs), stderr="".join(errs)
    )


def _run_single_process_baseline() -> dict[str, torch.Tensor]:
    torch.manual_seed(_fsdp_worker.SEED)
    model = AshuGPT(_fsdp_worker.MODEL_CONFIG)
    train_config = _fsdp_worker.build_train_config(
        batch_size=_fsdp_worker.NUM_EXAMPLES, checkpoint_interval=10**9
    )
    train_config.parallel = "ddp"  # world_size 1: no sharding to compare against, just the plain loop
    train(
        model,
        _fsdp_worker.build_dataset(),
        val_dataset=None,
        config=train_config,
        model_config=_fsdp_worker.MODEL_CONFIG,
    )
    return {name: p.detach().cpu().clone() for name, p in model.named_parameters()}


def test_fsdp_two_process_training_matches_single_process_baseline(tmp_path: Path) -> None:
    baseline_weights = _run_single_process_baseline()

    output_dir = tmp_path / "fsdp_out"
    output_dir.mkdir()
    result = _run_fsdp_worker(output_dir)
    assert result.returncode == 0, f"worker failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    checkpoints = list((output_dir / "checkpoints").glob("*.pt"))
    saved = torch.load(checkpoints[0], weights_only=True)["model_state_dict"]

    for name, expected in baseline_weights.items():
        # The saved weights were assembled from both ranks' shards, so if
        # either rank had optimized its half wrongly this comparison would
        # fail. Sharding is a memory layout, not a different optimization
        # problem, and this is the whole claim in one assertion.
        assert torch.allclose(saved[name], expected, atol=1e-5), f"{name}: differs from unsharded baseline"


def test_fsdp_actually_shards_rather_than_replicating(tmp_path: Path) -> None:
    """A wrapper that quietly replicated instead of sharding would pass
    every correctness test above -- it would just use DDP's memory. So
    check the shards directly: each rank should hold half of each
    parameter, and the two halves should reassemble into the saved model."""
    output_dir = tmp_path / "fsdp_out"
    output_dir.mkdir()
    result = _run_fsdp_worker(output_dir)
    assert result.returncode == 0, f"worker failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    rank0 = torch.load(output_dir / "rank0_shard.pt", weights_only=True)
    rank1 = torch.load(output_dir / "rank1_shard.pt", weights_only=True)
    saved = torch.load(next((output_dir / "checkpoints").glob("*.pt")), weights_only=True)["model_state_dict"]

    sharded_params = 0
    # Iterate the shards, not the saved state dict: this model ties its
    # input and output embeddings, so state_dict() reports the same tensor
    # under two names while named_parameters() -- and therefore the shard
    # files -- reports it once.
    for name in rank0:
        full = saved[name]
        local_elements = rank0[name].numel() + rank1[name].numel()
        assert local_elements == full.numel(), f"{name}: shards hold {local_elements} of {full.numel()} elements"
        if rank0[name].shape != full.shape:
            sharded_params += 1
            # FSDP2 shards on dim 0, so the halves concatenate back.
            assert torch.equal(torch.cat([rank0[name], rank1[name]], dim=0), full), f"{name}: shards do not reassemble"

    assert sharded_params > 0, "no parameter was actually split across ranks"


def test_fsdp_checkpoint_loads_into_plain_single_process_inference(tmp_path: Path) -> None:
    """The property that makes sharded training usable rather than a
    parallel universe: a checkpoint written by a 2-rank sharded run is an
    ordinary checkpoint. No "module." prefix, no DTensors, no record of how
    many GPUs made it -- `load_model_for_inference` has no idea."""
    from ashugpt.training.checkpoint import load_model_for_inference

    output_dir = tmp_path / "fsdp_out"
    output_dir.mkdir()
    result = _run_fsdp_worker(output_dir)
    assert result.returncode == 0, f"worker failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    checkpoints = list((output_dir / "checkpoints").glob("*.pt"))
    assert len(checkpoints) == 1, f"expected exactly one rank-0 checkpoint, got {checkpoints}"

    model = load_model_for_inference(checkpoints[0])
    assert model.num_parameters() == AshuGPT(_fsdp_worker.MODEL_CONFIG).num_parameters()

    # Plain tensors, not DTensor shards -- the distinction the whole
    # gather exists to erase.
    saved = torch.load(checkpoints[0], weights_only=True)["model_state_dict"]
    assert {type(v).__name__ for v in saved.values()} == {"Tensor"}
    assert not any(name.startswith("module.") for name in saved)


def test_fsdp_validation_does_not_deadlock(tmp_path: Path) -> None:
    """A forward pass under FSDP all-gathers parameters from every rank, so
    the trainer's rank-0-only validation would leave rank 0 waiting on
    shards that never arrive. The failure is a hang, not an exception,
    which is why this test asserts on completing at all -- the worker's
    timeout is what fails it."""
    output_dir = tmp_path / "fsdp_out"
    output_dir.mkdir()
    result = _run_fsdp_worker(output_dir, extra_args=["--validate", "--max-steps", "2"])
    assert result.returncode == 0, f"worker failed or hung:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "val_loss" in result.stdout


def test_fsdp_logs_and_checkpoints_only_from_rank_zero(tmp_path: Path) -> None:
    output_dir = tmp_path / "fsdp_out"
    output_dir.mkdir()
    result = _run_fsdp_worker(output_dir)
    assert result.returncode == 0, f"worker failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.count("train_loss") == 1
    assert result.stdout.count("Saved checkpoint") == 1


def test_fsdp_rejects_cpu_offload_without_fsdp() -> None:
    from ashugpt.config import TrainConfig

    with pytest.raises(ValueError, match="fsdp_cpu_offload"):
        TrainConfig(
            batch_size=1, seq_len=4, max_steps=1, warmup_steps=1, max_lr=1e-3, min_lr=1e-4,
            parallel="ddp", fsdp_cpu_offload=True,
        )
