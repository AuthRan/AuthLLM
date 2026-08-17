"""Unit tests for metrics-CSV bookkeeping across resume."""

import csv
from pathlib import Path

from ashugpt.training.trainer import _append_csv_row, _truncate_csv_after


def _write_log(path: Path, rows: list[dict]) -> None:
    for row in rows:
        _append_csv_row(path, row)


def _steps(path: Path) -> list[int]:
    with path.open(newline="", encoding="utf-8") as f:
        return [int(row["step"]) for row in csv.DictReader(f)]


def test_truncate_drops_rows_after_resume_point(tmp_path: Path) -> None:
    log = tmp_path / "metrics.csv"
    _write_log(log, [{"step": s, "train_loss": 1.0 / s, "lr": 1e-4} for s in (10, 20, 30, 40)])

    assert _truncate_csv_after(log, 20) == 2
    assert _steps(log) == [10, 20]


def test_truncate_keeps_rows_at_the_resume_step(tmp_path: Path) -> None:
    """A checkpoint at step N includes the rows logged at step N."""
    log = tmp_path / "metrics.csv"
    _write_log(
        log,
        [
            {"step": 10, "train_loss": 2.0, "lr": 1e-4},
            {"step": 10, "val_loss": 2.5, "val_perplexity": 12.2},
            {"step": 20, "train_loss": 1.5, "lr": 1e-4},
        ],
    )

    assert _truncate_csv_after(log, 10) == 1
    assert _steps(log) == [10, 10]


def test_truncate_preserves_header_and_values(tmp_path: Path) -> None:
    log = tmp_path / "metrics.csv"
    _write_log(
        log,
        [
            {"step": 10, "train_loss": 2.0, "lr": 3e-4},
            {"step": 20, "train_loss": 1.5, "lr": 2e-4},
        ],
    )
    _truncate_csv_after(log, 10)

    with log.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["step", "train_loss", "lr", "val_loss", "val_perplexity"]
        rows = list(reader)

    assert len(rows) == 1
    assert float(rows[0]["train_loss"]) == 2.0
    assert float(rows[0]["lr"]) == 3e-4


def test_truncate_is_idempotent_and_leaves_no_temp_file(tmp_path: Path) -> None:
    log = tmp_path / "metrics.csv"
    _write_log(log, [{"step": s, "train_loss": 1.0, "lr": 1e-4} for s in (10, 20, 30)])

    assert _truncate_csv_after(log, 10) == 2
    assert _truncate_csv_after(log, 10) == 0
    assert _steps(log) == [10]
    assert list(tmp_path.glob("*.tmp")) == []


def test_truncate_on_missing_file_is_a_noop(tmp_path: Path) -> None:
    assert _truncate_csv_after(tmp_path / "absent.csv", 100) == 0


def test_truncate_nothing_to_drop_leaves_file_untouched(tmp_path: Path) -> None:
    log = tmp_path / "metrics.csv"
    _write_log(log, [{"step": s, "train_loss": 1.0, "lr": 1e-4} for s in (10, 20)])
    original = log.read_text(encoding="utf-8")

    assert _truncate_csv_after(log, 20) == 0
    assert log.read_text(encoding="utf-8") == original


def test_resume_replay_does_not_duplicate_steps(tmp_path: Path) -> None:
    """The real failure: crash at 540, resume from 500, replay 520 and 540."""
    log = tmp_path / "metrics.csv"
    _write_log(log, [{"step": s, "train_loss": 4.6, "lr": 6e-4} for s in (500, 520, 540)])

    _truncate_csv_after(log, 500)
    _write_log(log, [{"step": s, "train_loss": 4.5, "lr": 6e-4} for s in (520, 540)])

    steps = _steps(log)
    assert steps == [500, 520, 540]
    assert len(steps) == len(set(steps))
