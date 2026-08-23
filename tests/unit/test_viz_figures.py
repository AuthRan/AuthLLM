"""Every number drawn on a figure has to be a number something measured.

Five of this repo's figures are built from `logs/*.csv`, so they cannot say
anything a run did not record. The rest are bar charts of scored comparisons,
and those numbers do not live in a CSV -- they live in the tables under
`results/`, and they were copied by hand into module-level tables in
`ashugpt.viz.figures`. `figures.py` says that arrangement means "a figure and
its prose cannot drift apart silently". Nothing checked it, and it had already
drifted: the 5.0e-6 row of the DPO sweep was drawn at 94% stop / 30% loop / 88
tokens against a measured 98% / 22% / 79, so a figure in the README reported a
checkpoint behaving worse than it did.

This file is the check that claim needs. It reads the same `results/*.md`
tables the scoring scripts wrote and asserts the hand-copied numbers still
agree with them, so the next edit to either side fails here rather than in a
picture nobody re-reads.

A test that re-typed the expected numbers would only be a second copy to drift.
The point is the *link* to the generated file, so every expectation below is a
lookup into one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ashugpt.viz.figures import BEHAVIOUR, CHAT_SWEEP, DPO_SWEEP, PRESETS, SFT_BASELINE

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS = REPO_ROOT / "results"

ROW = re.compile(r"^\|(.+)\|\s*$", re.M)


def _cells(line: str) -> list[str]:
    return [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")]


def _table(path: Path) -> dict[str, dict[str, str]]:
    """The first markdown table in a results file, keyed by checkpoint name.

    Every file under results/ that this reads is written by a scoring script
    with the checkpoint in the leading column, so the first table is the
    scored comparison and anything after it is generated samples.
    """
    header: list[str] | None = None
    table: dict[str, dict[str, str]] = {}
    for match in ROW.finditer(path.read_text(encoding="utf-8")):
        cells = _cells(match.group(1))
        if all(set(c) <= set("-: ") for c in cells):  # the |---|---:| separator
            continue
        if header is None:
            header = cells
            continue
        if len(cells) != len(header):
            break  # a different table further down the file
        label = cells[0].strip("`")
        table[label] = dict(zip(header, cells))
    assert header is not None, f"no markdown table in {path.name}"
    return table


def _number(value: str) -> float:
    """`98%` and `2.7444` both come out as floats; the unit is the column's."""
    return float(value.replace("%", "").replace(",", "").strip())


@pytest.fixture(scope="module")
def dolly_sweep() -> dict[str, dict[str, str]]:
    """Instruction-following behaviour for sft and the three DPO checkpoints."""
    return _table(RESULTS / "instruction_eval_dpo_sweep.md")


@pytest.fixture(scope="module")
def preference_sweep() -> dict[str, dict[str, str]]:
    """Preference ranking for the same four checkpoints."""
    return _table(RESULTS / "preference_eval_sweep.md")


# --- the table parser itself, before anything trusts it ----------------------


def test_results_tables_parse_into_the_expected_checkpoints(dolly_sweep, preference_sweep):
    """If the parser silently returned {} every assertion below would pass."""
    assert set(dolly_sweep) == {"sft", "dpo_1e6", "dpo_5e6", "dpo_2e5"}
    assert set(preference_sweep) == {"sft", "dpo_1e6", "dpo_5e6", "dpo_2e5"}
    assert _number(dolly_sweep["sft"]["stop rate"]) == 92
    assert _number(preference_sweep["sft"]["DPO accuracy"]) == 50.0


def test_bolded_cells_survive_parsing(dolly_sweep):
    """The scoring scripts bold the best cell per column -- `**98%**`."""
    assert _number(dolly_sweep["dpo_1e6"]["stop rate"]) == 98


# --- figure 05: the DPO sweep, ranked twice ----------------------------------

DPO_ROWS = ["dpo_1e6", "dpo_5e6", "dpo_2e5"]


def test_dpo_sweep_preference_numbers_match_the_scored_table(preference_sweep):
    for (label, dpo_acc, per_token, _stop, _loop, _tokens), key in zip(DPO_SWEEP, DPO_ROWS):
        row = preference_sweep[key]
        assert dpo_acc == _number(row["DPO accuracy"]), label
        assert per_token == _number(row["per-token accuracy"]), label


def test_dpo_sweep_behaviour_numbers_match_the_scored_table(dolly_sweep):
    """The regression: 5.0e-6 was drawn at 94/30/88 against a measured 98/22/79."""
    for (label, _dpo_acc, _per_token, stop, loop, tokens), key in zip(DPO_SWEEP, DPO_ROWS):
        row = dolly_sweep[key]
        assert stop == _number(row["stop rate"]), label
        assert loop == _number(row["loop rate"]), label
        assert tokens == _number(row["mean tokens"]), label


def test_the_sft_baseline_is_the_same_model_in_both_tables(dolly_sweep, preference_sweep):
    """Figure 05 draws one reference line across both panels.

    It is only one line if both panels' baselines are the same checkpoint,
    which is the whole reason the two tables carry an `sft` row at all.
    """
    dpo_acc, per_token, stop, loop, tokens = SFT_BASELINE
    assert dpo_acc == _number(preference_sweep["sft"]["DPO accuracy"])
    assert per_token == _number(preference_sweep["sft"]["per-token accuracy"])
    assert stop == _number(dolly_sweep["sft"]["stop rate"])
    assert loop == _number(dolly_sweep["sft"]["loop rate"])
    assert tokens == _number(dolly_sweep["sft"]["mean tokens"])


def test_the_two_rankings_really_do_reverse():
    """The figure's title is a claim; this is the claim.

    If a rerun ever made preference accuracy and behaviour agree, the figure
    would still be drawn and still be captioned "ranked twice ... in opposite
    orders", which would be the drift that matters most.
    """
    by_objective = [d[1] for d in DPO_SWEEP]
    assert by_objective == sorted(by_objective), "more learning rate, more DPO accuracy"
    loop_rates = [d[4] for d in DPO_SWEEP]
    lengths = [d[5] for d in DPO_SWEEP]
    assert loop_rates == sorted(loop_rates), "and more looping"
    assert lengths == sorted(lengths), "and longer answers"


# --- figure 03: every stage on the one split they share ----------------------

# Each row of BEHAVIOUR, and the generated table its numbers came from. They
# are different files because no single scoring run covers all four
# checkpoints -- which is exactly why the columns have to be checked to be
# the same measurement rather than assumed to be.
BEHAVIOUR_SOURCES = {
    "base 124M": ("instruction_eval_dolly.md", "base"),
    "instruction-tuned": ("instruction_eval_dpo_sweep.md", "sft"),
    "chat branch": ("instruction_eval_chat.md", "chat_1epoch"),
    "preference branch": ("instruction_eval_dpo_sweep.md", "dpo_1e6"),
}


def test_stage_behaviour_numbers_match_their_scored_tables():
    for name, _note, stop, loop, tokens, _color in BEHAVIOUR:
        filename, key = BEHAVIOUR_SOURCES[name]
        row = _table(RESULTS / filename)[key]
        assert stop == _number(row["stop rate"]), name
        assert loop == _number(row["loop rate"]), name
        assert tokens == _number(row["mean tokens"]), name


def test_stage_behaviour_rows_are_all_the_same_measurement():
    """One axis, so one split and one generation setting for every bar.

    Four bars from three files is only honest if the files describe the same
    eval. Each states its split and its sampling settings in the line under
    its title, and those lines have to be identical.
    """
    preambles = set()
    for filename, _key in BEHAVIOUR_SOURCES.values():
        text = (RESULTS / filename).read_text(encoding="utf-8")
        held_out = [ln for ln in text.splitlines() if "held-out examples" in ln]
        assert held_out, f"{filename} does not say what it scored"
        preambles.add(held_out[0].strip())
    assert len(preambles) == 1, f"figure 03 puts different evals on one axis: {preambles}"


# --- figure 04: the chat learning-rate sweep ---------------------------------

CHAT_ROWS = ["chat_15e6", "chat_4e5", "chat_1e4", "chat_25e4"]


def test_chat_sweep_final_losses_match_the_scored_table():
    table = _table(RESULTS / "chat_eval.md")
    for (label, _stem, final_loss), key in zip(CHAT_SWEEP, CHAT_ROWS):
        assert final_loss == _number(table[key]["held-out loss"]), label


def test_chat_sweep_curves_come_from_logs_that_exist():
    """"If a log is missing the figure fails loudly" -- here rather than there.

    scripts/plot_results.py promises no synthetic data path, so a stem naming
    a CSV that is not in the tree is a broken figure, and it should not take
    a plotting run to find that out.
    """
    for label, stem, _final in CHAT_SWEEP:
        assert (REPO_ROOT / "logs" / f"{stem}.csv").exists(), f"{label} -> logs/{stem}.csv"
    assert (REPO_ROOT / "logs" / "sft_chat.csv").exists(), "the epoch that shipped"


def test_the_swept_minimum_is_bracketed():
    """The right-hand panel is titled "A bracketed minimum, not an edge"."""
    finals = [c[2] for c in CHAT_SWEEP]
    best = finals.index(min(finals))
    assert 0 < best < len(finals) - 1, "the winner is at an end, so the sweep was not wide enough"


# --- figure 02: the scaling ladder -------------------------------------------


def _readme_preset_table() -> dict[str, dict[str, str]]:
    """Section 4's preset table, which is keyed by preset rather than checkpoint."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## 4. Model Configuration Presets", 1)[1].split("\n## ", 1)[0]
    header: list[str] | None = None
    table: dict[str, dict[str, str]] = {}
    for match in ROW.finditer(section):
        cells = _cells(match.group(1))
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if header is None:
            header = cells
            continue
        table[cells[0].strip("`")] = dict(zip(header, cells))
    assert header is not None, "README section 4 has no table"
    return table


def test_preset_bars_match_the_table_README_prints():
    """The bars now come from the configs, so this checks them against prose.

    README section 4 prints every preset's exact parameter count, and the
    figure is the same four numbers drawn instead of tabulated. Deriving them
    means the figure cannot disagree with the *configs*; it can still
    disagree with the README, and that is the copy a reader compares it to.
    """
    table = _readme_preset_table()
    assert set(table) == {name for name, *_ in PRESETS}, "section 4 lists other presets"
    for name, layers, d_model, params, _status in PRESETS:
        row = table[name]
        assert layers == _number(row["Layers"]), name
        assert d_model == _number(row["`d_model`"]), name
        assert params == _number(row["Parameters (exact)"]), name


def test_the_ladder_is_drawn_in_increasing_size():
    counts = [p[3] for p in PRESETS]
    assert counts == sorted(counts)


# --- a figure nobody can see -------------------------------------------------


def test_every_figure_built_is_a_figure_shown():
    """A figure that no document embeds is work nobody sees.

    03-stage-behaviour.png was generated by scripts/plot_results.py, checked
    by the tests above against the tables it was drawn from, and embedded
    nowhere at all -- so the one picture of what each fine-tuning stage did
    to behaviour existed in the repo and appeared in no document for a day.
    Building it is not the deliverable; showing it is.
    """
    from ashugpt.viz.figures import ALL

    docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [REPO_ROOT / "README.md", REPO_ROOT / "SPEC.md"]
        + sorted((REPO_ROOT / "results").glob("*.md"))
        + sorted((REPO_ROOT / "learning").glob("*.md"))
    )

    built = sorted((REPO_ROOT / "resources" / "plots").glob("*.png"))
    assert len(built) == len(ALL), "every entry in figures.ALL should have been drawn"

    unshown = [f.name for f in built if f.name not in docs]
    assert not unshown, f"drawn but embedded in no document: {unshown}"
