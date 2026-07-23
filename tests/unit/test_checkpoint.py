"""Unit tests for checkpoint save/resume."""

from pathlib import Path

import torch

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.model.gpt import AshuGPT
from ashugpt.training.checkpoint import load_checkpoint, save_checkpoint
from ashugpt.training.optim import build_optimizer


def small_model_config() -> ModelConfig:
    return ModelConfig(
        name="test", vocab_size=32, d_model=16, n_layers=2, n_heads=2, n_kv_heads=2, d_ff=32, context_length=16
    )


def base_train_config() -> TrainConfig:
    return TrainConfig(batch_size=2, seq_len=8, max_steps=10, warmup_steps=2, max_lr=1e-3, min_lr=1e-4)


def _train_one_step(model: torch.nn.Module, optimizer: torch.optim.Optimizer, vocab_size: int) -> None:
    x = torch.randint(0, vocab_size, (2, 8))
    y = torch.randint(0, vocab_size, (2, 8))
    out = model(x, labels=y)
    out.loss.backward()
    optimizer.step()
    optimizer.zero_grad()


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    model_config, train_config = small_model_config(), base_train_config()
    torch.manual_seed(0)
    model = AshuGPT(model_config)
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.1)

    path = tmp_path / "nested" / "dir" / "ckpt.pt"
    save_checkpoint(path, model, optimizer, step=1, model_config=model_config, train_config=train_config)
    assert path.exists()


def test_load_restores_step(tmp_path: Path) -> None:
    model_config, train_config = small_model_config(), base_train_config()
    torch.manual_seed(0)
    model = AshuGPT(model_config)
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.1)

    path = tmp_path / "ckpt.pt"
    save_checkpoint(path, model, optimizer, step=7, model_config=model_config, train_config=train_config)

    fresh_model = AshuGPT(model_config)
    fresh_optimizer = build_optimizer(fresh_model, lr=1e-3, weight_decay=0.1)
    resumed_step = load_checkpoint(path, fresh_model, fresh_optimizer)
    assert resumed_step == 7


def test_load_restores_model_weights_exactly(tmp_path: Path) -> None:
    model_config, train_config = small_model_config(), base_train_config()
    torch.manual_seed(0)
    model = AshuGPT(model_config)
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.1)
    _train_one_step(model, optimizer, model_config.vocab_size)  # move weights away from init

    path = tmp_path / "ckpt.pt"
    save_checkpoint(path, model, optimizer, step=1, model_config=model_config, train_config=train_config)

    torch.manual_seed(999)  # different init -- proves load actually overwrites it
    fresh_model = AshuGPT(model_config)
    fresh_optimizer = build_optimizer(fresh_model, lr=1e-3, weight_decay=0.1)
    load_checkpoint(path, fresh_model, fresh_optimizer)

    for p1, p2 in zip(model.parameters(), fresh_model.parameters()):
        assert torch.equal(p1, p2)


def test_load_restores_optimizer_momentum(tmp_path: Path) -> None:
    model_config, train_config = small_model_config(), base_train_config()
    torch.manual_seed(0)
    model = AshuGPT(model_config)
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.1)
    _train_one_step(model, optimizer, model_config.vocab_size)  # populates AdamW's exp_avg buffers

    path = tmp_path / "ckpt.pt"
    save_checkpoint(path, model, optimizer, step=1, model_config=model_config, train_config=train_config)

    fresh_model = AshuGPT(model_config)
    fresh_optimizer = build_optimizer(fresh_model, lr=1e-3, weight_decay=0.1)
    load_checkpoint(path, fresh_model, fresh_optimizer)

    original_state = list(optimizer.state.values())
    loaded_state = list(fresh_optimizer.state.values())
    assert len(original_state) == len(loaded_state) > 0
    for orig, loaded in zip(original_state, loaded_state):
        assert torch.equal(orig["exp_avg"], loaded["exp_avg"])
        assert torch.equal(orig["exp_avg_sq"], loaded["exp_avg_sq"])


def test_loaded_model_gives_identical_loss_on_same_batch(tmp_path: Path) -> None:
    model_config, train_config = small_model_config(), base_train_config()
    torch.manual_seed(0)
    model = AshuGPT(model_config)
    optimizer = build_optimizer(model, lr=1e-3, weight_decay=0.1)
    _train_one_step(model, optimizer, model_config.vocab_size)

    path = tmp_path / "ckpt.pt"
    save_checkpoint(path, model, optimizer, step=1, model_config=model_config, train_config=train_config)

    fresh_model = AshuGPT(model_config)
    load_checkpoint(path, fresh_model)

    x = torch.randint(0, model_config.vocab_size, (2, 8))
    y = torch.randint(0, model_config.vocab_size, (2, 8))
    model.eval()
    fresh_model.eval()
    with torch.no_grad():
        loss_original = model(x, labels=y).loss
        loss_loaded = fresh_model(x, labels=y).loss
    assert torch.allclose(loss_original, loss_loaded)
