"""Unit tests for the inspect_model CLI (Milestone 10)."""

import subprocess
import sys

import pytest

from ashugpt.config import load_model_config
from ashugpt.inspect_model import PRESET_ALIASES, PRESET_DIR, PRESET_NAMES, _resolve_config_path
from ashugpt.utils.memory import estimate_memory


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_resolve_config_path_finds_every_builtin_preset(preset: str) -> None:
    path = _resolve_config_path(preset)
    assert path.exists()
    assert load_model_config(path).name == preset


@pytest.mark.parametrize("alias,target", list(PRESET_ALIASES.items()))
def test_resolve_config_path_accepts_aliases(alias: str, target: str) -> None:
    assert _resolve_config_path(alias) == PRESET_DIR / f"{target}.yaml"


def test_resolve_config_path_rejects_unknown_name() -> None:
    with pytest.raises(SystemExit):
        _resolve_config_path("not-a-real-preset")


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_every_preset_can_be_estimated_without_building_a_model(preset: str) -> None:
    # This is the actual point of the milestone: a report must be
    # producible for every preset, including xl_1b, with nothing built.
    config = load_model_config(_resolve_config_path(preset))
    estimate = estimate_memory(config, batch_size=1, seq_len=config.context_length)
    assert estimate.param_count == config.approx_param_count()
    assert estimate.total_training_memory > 0


def test_cli_reports_1b_config_is_not_a_trained_model() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ashugpt.inspect_model", "--config", "1b"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    output = result.stdout

    # The explicit requirement: unmissable that this is a config, not a
    # trained model.
    assert "ARCHITECTURE CONFIGURATION" in output
    assert "not a trained model" in output
    assert "1,233,479,680" in output  # xl_1b's exact parameter count

    # The three-way distinction must actually be present, not just implied.
    assert "Implemented architecture" in output
    assert "Model trained from scratch" in output
    assert "Pretrained checkpoint loaded for inference" in output


def test_cli_reports_required_fields_for_every_config() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ashugpt.inspect_model", "--config", "small"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    output = result.stdout
    for required in [
        "Layers:",
        "Hidden dimension:",
        "Attention heads:",
        "Vocabulary size:",
        "Context length:",
        "Parameter count:",
        "Weight memory (FP32):",
        "Weight memory (BF16):",
        "Optimizer memory:",
    ]:
        assert required in output, f"missing '{required}' in CLI output"


def test_cli_all_flag_reports_every_preset() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ashugpt.inspect_model", "--all"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    for preset in PRESET_NAMES:
        assert f"=== {preset} ===" in result.stdout


def test_cli_requires_config_or_all() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ashugpt.inspect_model"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
