"""Pulling two series out of one CSV, without letting them drift apart.

`ashugpt.viz.logs` exists because a trainer writes a step that was evaluated
as *two* rows -- one carrying train_loss and a blank val_loss, one the
reverse -- so every figure has to split them the same way. The failure this
file is mostly about is what happens when a column inside one of those rows
is blank too.

`lr` already handled it: a train row with no learning rate appends a NaN, so
`lr[i]` keeps meaning `train_steps[i]`. `val_perplexity` did not, and it
skipped the append instead, which silently shortens the list -- index i stops
meaning step i and nothing raises. That is not hypothetical: the trainer
deliberately writes an empty perplexity whenever the loss is not a
cross-entropy, so all six DPO logs in `logs/` were already parsing to 15
validation losses and 0 perplexities. Nothing had zipped the two yet, which
is the only reason no figure was wrong.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from ashugpt.viz.logs import TrainingLog, load_log

REPO_ROOT = Path(__file__).resolve().parents[2]
HEADER = "step,train_loss,lr,val_loss,val_perplexity\n"


def _write(tmp_path: Path, body: str, name: str = "run.csv") -> Path:
    path = tmp_path / name
    path.write_text(HEADER + body, encoding="utf-8")
    return path


# --- the two-rows-per-step shape --------------------------------------------


def test_train_and_eval_rows_become_separate_series(tmp_path):
    """A step that was evaluated appears once in each series, not twice in one."""
    log = load_log(
        _write(
            tmp_path,
            "10,3.5,0.001,,\n"
            "20,3.0,0.002,,\n"
            "20,,,2.8,16.44\n",
        )
    )
    assert log.train_steps == [10, 20]
    assert log.train_loss == [3.5, 3.0]
    assert log.val_steps == [20]
    assert log.val_loss == [2.8]
    # Step 20 is in both series exactly once -- the eval row must not also be
    # read as a training point at loss 0.0.
    assert log.train_steps.count(20) == 1


def test_blank_metric_is_absent_rather_than_zero(tmp_path):
    """An empty cell is "not measured", and float("") would be a crash anyway.

    The dangerous reading is not the crash -- it is treating blank as 0.0,
    which puts a perfect loss on the curve at every unevaluated step.
    """
    log = load_log(_write(tmp_path, "10,3.5,0.001,,\n"))
    assert log.val_loss == []
    assert log.val_steps == []
    assert log.final_val_loss is None


def test_name_defaults_to_the_file_stem(tmp_path):
    path = _write(tmp_path, "10,3.5,0.001,,\n", name="sft_chat.csv")
    assert load_log(path).name == "sft_chat"
    assert load_log(path, "one epoch").name == "one epoch"


# --- index alignment: the bug this file was written for ----------------------


def test_missing_perplexity_keeps_val_series_aligned(tmp_path):
    """val_perplexity[i] must describe val_steps[i], evaluated or not.

    This is the regression. A DPO run logs a val_loss with no perplexity, and
    dropping those entries left the two lists at different lengths, so pairing
    them by position silently reported one step's perplexity against another
    step's loss.
    """
    log = load_log(
        _write(
            tmp_path,
            "10,,,2.9,18.17\n"
            "20,,,2.8,\n"       # measured, but no perplexity to report
            "30,,,2.7,14.88\n",
        )
    )
    assert log.val_steps == [10, 20, 30]
    assert log.val_loss == [2.9, 2.8, 2.7]
    assert len(log.val_perplexity) == len(log.val_steps)
    assert log.val_perplexity[0] == 18.17
    assert math.isnan(log.val_perplexity[1])
    assert log.val_perplexity[2] == 14.88


def test_missing_lr_keeps_train_series_aligned(tmp_path):
    """The same invariant on the train side, which already held."""
    log = load_log(_write(tmp_path, "10,3.5,0.001,,\n20,3.4,,,\n"))
    assert len(log.lr) == len(log.train_steps) == 2
    assert log.lr[0] == 0.001
    assert math.isnan(log.lr[1])


def test_final_val_perplexity_is_none_rather_than_nan(tmp_path):
    """A run with no perplexity anywhere reports None, not a truthy NaN.

    `val_perplexity[-1]` is now always populated, so the obvious
    `if log.val_perplexity[-1]:` guard passes on a NaN and annotates a figure
    with the literal string "nan". The property is what callers should use.
    """
    dpo_shaped = load_log(_write(tmp_path, "10,,,0.69,\n20,,,0.67,\n"))
    assert len(dpo_shaped.val_perplexity) == 2
    assert dpo_shaped.final_val_perplexity is None
    assert dpo_shaped.final_val_loss == 0.67

    with_ppl = load_log(_write(tmp_path, "10,,,2.9,18.17\n", name="b.csv"))
    assert with_ppl.final_val_perplexity == 18.17


def test_empty_log_reports_nothing_rather_than_raising(tmp_path):
    """A header-only CSV is what a run that died before its first log left."""
    log = load_log(_write(tmp_path, ""))
    assert log.train_steps == [] and log.val_steps == []
    assert log.final_val_loss is None
    assert log.final_val_perplexity is None
    assert log.max_step == 0
    assert log.smoothed_train_loss(window=40) == []


# --- smoothing ---------------------------------------------------------------


def test_window_of_one_returns_the_raw_series():
    """The single-run plots draw the raw curve under the smoothed one."""
    log = TrainingLog(name="t", path=Path("t.csv"), train_loss=[3.0, 2.0, 1.0])
    assert log.smoothed_train_loss(window=1) == [3.0, 2.0, 1.0]
    assert log.smoothed_train_loss(window=0) == [3.0, 2.0, 1.0]


def test_smoothing_is_a_trailing_mean_that_starts_immediately():
    """The first points average over what exists, rather than being dropped.

    A leading gap would misalign the smoothed series against train_steps,
    which is the same class of bug as the perplexity one above.
    """
    log = TrainingLog(name="t", path=Path("t.csv"), train_loss=[4.0, 2.0, 6.0, 0.0])
    assert log.smoothed_train_loss(window=2) == [4.0, 3.0, 4.0, 3.0]
    assert len(log.smoothed_train_loss(window=40)) == 4


def test_smoothing_does_not_mutate_the_underlying_series():
    log = TrainingLog(name="t", path=Path("t.csv"), train_loss=[4.0, 2.0])
    log.smoothed_train_loss(window=1).append(99.0)
    assert log.train_loss == [4.0, 2.0]


# --- against the logs actually in the tree -----------------------------------


@pytest.mark.parametrize(
    "name",
    ["medium_metrics.csv", "sft_chat.csv", "dpo_hh.csv", "sft_alpaca_packed_30.csv"],
)
def test_real_logs_parse_with_aligned_series(name):
    """The invariant, checked against files a real run wrote.

    dpo_hh.csv is in this list on purpose: it is the shape that broke, with a
    validation loss at every eval and a perplexity at none of them.
    """
    path = REPO_ROOT / "logs" / name
    if not path.exists():  # pragma: no cover - the logs ship with the repo
        pytest.skip(f"{name} is not in this checkout")
    log = load_log(path)
    assert log.train_steps, "a real run logged at least one training step"
    assert len(log.lr) == len(log.train_loss) == len(log.train_steps)
    assert len(log.val_perplexity) == len(log.val_loss) == len(log.val_steps)
    # Deliberately not asserting the steps are sorted or unique. They are not:
    # medium_metrics.csv carries three rows for step 520 and two for 540, from
    # restarts that predate _truncate_csv_after() in trainer.py. The parser's
    # job is to report what the file says, so that belongs in the next test
    # rather than being asserted away here.
    assert log.max_step == max(log.train_steps)


def test_the_dpo_log_is_the_case_that_regressed():
    """Guarding the guard: if this stops being true, the test above got weaker."""
    path = REPO_ROOT / "logs" / "dpo_hh.csv"
    if not path.exists():  # pragma: no cover - the logs ship with the repo
        pytest.skip("dpo_hh.csv is not in this checkout")
    log = load_log(path)
    assert log.val_loss, "the DPO run evaluated"
    assert all(math.isnan(p) for p in log.val_perplexity), (
        "DPO's loss is not a cross-entropy, so the trainer writes no perplexity"
    )
    assert log.final_val_perplexity is None
