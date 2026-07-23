"""Integration test: real DistributedDataParallel training across two
actual OS processes (gloo backend, CPU -- there's no GPU in this
environment, but gloo is the same torch.distributed machinery NCCL uses on
GPU, just a different backend; the training code in trainer.py doesn't
know or care which one is active).

Processes are launched manually (subprocess.Popen with RANK/LOCAL_RANK/
WORLD_SIZE/MASTER_ADDR/MASTER_PORT set directly) rather than via `torchrun`
itself: this dev environment's CPU-only Windows torch build fails
torchrun's own elastic rendezvous with a libuv/TCPStore error unrelated to
anything in this project (see SPEC.md). Setting those env vars by hand and
calling dist.init_process_group(backend="gloo") -- exactly what
ashugpt/training/ddp.py does, and exactly what torchrun would set up for
us -- sidesteps that broken code path entirely while still exercising the
*same* DDP code this project ships. README.md's documented launch command
is plain `torchrun`, which is expected to work fine in a normal
Linux/CUDA deployment.

Mathematical setup: tests/integration/_ddp_worker.py's dataset has exactly
8 examples, split evenly across 2 ranks (4 each) by DistributedSampler --
no padding/duplication, since 8 is evenly divisible by world_size=2. DDP
averages each rank's *mean* gradient over its 4 examples; with equal group
sizes, mean(mean_A, mean_B) == mean(A union B) exactly (a property of the
mean, independent of which specific examples land in which half). So a
single *un-distributed* training step over the whole 8-example dataset as
one batch is an exact mathematical baseline for what the 2-process DDP
step should produce -- not an approximation, just algebra.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import torch

from ashugpt.model.gpt import AshuGPT
from ashugpt.training.trainer import train

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ddp_worker  # noqa: E402  -- shares the fixed scenario with the worker script

WORKER_SCRIPT = Path(__file__).resolve().parent / "_ddp_worker.py"
WORLD_SIZE = 2


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_ddp_worker(output_dir: Path) -> subprocess.CompletedProcess:
    """Launches WORLD_SIZE independent processes running _ddp_worker.py,
    with the env vars torchrun would normally set, and waits for both."""
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
            }
        )
        procs.append(
            subprocess.Popen(
                [sys.executable, str(WORKER_SCRIPT), "--output-dir", str(output_dir)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    outs, errs, codes = [], [], []
    for p in procs:
        out, err = p.communicate(timeout=60)
        outs.append(out)
        errs.append(err)
        codes.append(p.returncode)

    combined_stdout = "".join(outs)
    combined_stderr = "".join(errs)
    return subprocess.CompletedProcess(
        args=[str(WORKER_SCRIPT)], returncode=max(codes), stdout=combined_stdout, stderr=combined_stderr
    )


def _run_single_process_baseline() -> dict[str, torch.Tensor]:
    torch.manual_seed(_ddp_worker.SEED)
    model = AshuGPT(_ddp_worker.MODEL_CONFIG)
    train_config = _ddp_worker.build_train_config(batch_size=_ddp_worker.NUM_EXAMPLES, checkpoint_interval=10**9)
    train(model, _ddp_worker.build_dataset(), val_dataset=None, config=train_config, model_config=_ddp_worker.MODEL_CONFIG)
    return {name: p.detach().clone() for name, p in model.named_parameters()}


def test_ddp_two_process_training_matches_single_process_baseline(tmp_path: Path) -> None:
    baseline_weights = _run_single_process_baseline()

    output_dir = tmp_path / "ddp_out"
    output_dir.mkdir()
    result = _run_ddp_worker(output_dir)
    assert result.returncode == 0, f"worker process failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    rank0_weights = torch.load(output_dir / "rank0_weights.pt", weights_only=True)
    rank1_weights = torch.load(output_dir / "rank1_weights.pt", weights_only=True)

    for name in baseline_weights:
        # The two DDP ranks saw disjoint data but must land on identical
        # weights -- that's only possible if gradient synchronization
        # actually happened before the optimizer step.
        assert torch.equal(rank0_weights[name], rank1_weights[name]), f"{name}: ranks diverged"
        # And that synchronized result must match the single-process
        # baseline computed over the combined data (see module docstring).
        assert torch.allclose(rank0_weights[name], baseline_weights[name], atol=1e-5), f"{name}: baseline mismatch"


def test_ddp_logs_and_checkpoints_only_from_rank_zero(tmp_path: Path) -> None:
    output_dir = tmp_path / "ddp_out"
    output_dir.mkdir()
    result = _run_ddp_worker(output_dir)
    assert result.returncode == 0, f"worker process failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    # Both ranks run the same code, but only rank 0 should ever print
    # training progress or save a checkpoint -- if this fired from every
    # rank, the line/file would appear world_size times, not once.
    assert result.stdout.count("train_loss") == 1
    assert result.stdout.count("Saved checkpoint") == 1

    checkpoint_files = list((output_dir / "checkpoints").glob("*.pt"))
    assert len(checkpoint_files) == 1
