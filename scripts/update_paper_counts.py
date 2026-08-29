"""Rewrite the derived numbers in paper/paper.md from the results files.

These two numbers drift every time a sweep lands, and a paper that misstates how
much compute produced it is a paper that will misstate other things. Rather than
hand-patch them, regenerate:

    python scripts/update_paper_counts.py

It rewrites exactly two spans, both marked in the markdown so the edit is
idempotent and cannot wander:

    <!--runs-->...<!--/runs-->        the status block's run count
    <!--compute-->...<!--/compute-->  Appendix D's wall-clock paragraph
    <!--exponents-->...<!--/exponents-->  Appendix F's table of every comparison

The last is regenerated from results/exponents.csv rather than transcribed, so
the one table the paper's central claim can be audited from cannot drift out of
step with the estimator that produced it.

If the markers are missing the script says so and changes nothing, rather than
guessing where the numbers live.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Both versions of the paper are rewritten, because both quote the same
# regenerated numbers and a workshop submission built from a stale table is
# the failure this script exists to prevent. The workshop version carries a
# subset of the markers: it has no status block, so no <!--runs-->.
PAPERS = [REPO / "paper" / "paper.md", REPO / "paper" / "workshop.md"]
# One entry per base model the sweep was run against. Keep this in step with
# `export_exponents.py`: a ledger missing here is compute the paper spent and
# does not admit to.
LEDGERS = [
    (REPO / "results" / "lr_scaling_sweep.csv", "124M"),
    (REPO / "results" / "lr_scaling_small.csv", "30M"),
    (REPO / "results" / "lr_scaling_small9k.csv", "30M at the matched budget"),
    (REPO / "results" / "lr_scaling_mini.csv", "7M"),
    (REPO / "results" / "lr_scaling_mini2k.csv", "7M at the matched budget"),
    (REPO / "results" / "lr_scaling_ckpt.csv", "124M grid extension"),
    # The 124M model fine-tuned from an early pretraining checkpoint, matched on
    # perplexity to the 7M model rather than on parameter count: the control that
    # separates model size from base-model quality (section 4.7.2).
    (REPO / "results" / "lr_scaling_quality.csv", "124M at the 7M model's quality"),
    # The middle point of the same quality axis, at perplexity 39.4.
    (REPO / "results" / "lr_scaling_quality2500.csv", "124M at perplexity 39.4"),
]


def tally() -> tuple[int, int, float, list[str]]:
    total = timed = 0
    seconds = 0.0
    notes = []
    for path, label in LEDGERS:
        if not path.exists():
            continue
        rows = list(csv.DictReader(path.open()))
        if not rows:
            continue
        t = sum(1 for r in rows if r.get("wall_seconds"))
        total += len(rows)
        timed += t
        seconds += sum(float(r["wall_seconds"]) for r in rows if r.get("wall_seconds"))
        notes.append(f"{len(rows)} at {label}")
    return total, timed, seconds / 3600.0, notes


# How each ledger's base model should read in Appendix F, and the order the rows
# are grouped in: the three model sizes largest-first, then the controls. The
# controls sit last because none of them is a fourth size -- two hold the size
# and halve the pretraining budget, and the last two hold the size at 124M and
# take the base model down the quality axis, to perplexity 39.4 and then to the
# 7M model's 107.0. Those two are ordered by how far down that axis they go.
MODEL_ORDER = ["124M", "30M", "7M", "30M@19.7", "7M@18.0", "124M@ppl39", "124M@ppl107"]
MODEL_LABEL = {
    "124M": "124M",
    "30M": "30M",
    "7M": "7M",
    "30M@19.7": "30M @ 19.7 tok/param",
    "7M@18.0": "7M @ 18.0 tok/param",
    "124M@ppl39": "124M @ perplexity 39.4",
    "124M@ppl107": "124M @ perplexity 107",
}
CORPUS_LABEL = {
    "alpaca": "Alpaca, whole", "alpaca_third": "Alpaca, random third",
    "alpaca_ninth": "Alpaca, random ninth", "alpaca_short": "Alpaca, short tercile",
    "alpaca_mid": "Alpaca, middle tercile", "alpaca_long": "Alpaca, long tercile",
    "dolly": "Dolly, whole", "dolly_third": "Dolly, random third",
}


def exponent_table() -> str:
    """Appendix F, straight from results/exponents.csv.

    Sorted by model (largest first) and then by the size of the run, so the
    scale trend of section 4.6 reads down each group and the model-size trend of
    4.7 reads across them.
    """
    path = REPO / "results" / "exponents.csv"
    if not path.exists():
        raise SystemExit(f"{path} missing; run scripts/export_exponents.py first")
    rows = list(csv.DictReader(path.open()))
    lines = [
        "",
        "| base model | corpus | examples | `p` | lr* padded | lr* packed | shift | exponent | seeds |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in MODEL_ORDER:
        group = [r for r in rows if r["model"] == model]
        # Alpaca rows before Dolly rows, each by the size of the run, so the
        # scale trend reads down a corpus rather than zig-zagging between two.
        group.sort(key=lambda r: (not r["dataset"].startswith("alpaca"),
                                  int(r["train_examples"] or 0)))
        for r in group:
            # A "bound" computed from one curve is zero by construction, not
            # small. Rendering it as +/- 0.000 would claim the tightest interval
            # in the table for the least replicated row, which is how the 7M
            # random third briefly carried a +/- 0.006 (Appendix C). Single-seed
            # rows print the point estimate and say they are provisional.
            seeds = int(r["seeds"])
            if seeds < 2:
                cell = f"**{float(r['exponent']):.3f}** (no bound)"
                seed_note = "1, provisional"
            else:
                cell = f"**{float(r['exponent']):.3f} ± {float(r['bound']):.3f}**"
                seed_note = str(seeds)
            lines.append(
                f"| {MODEL_LABEL.get(model, model)} "
                f"| {CORPUS_LABEL.get(r['dataset'], r['dataset'])} "
                f"| {int(r['train_examples']):,} "
                f"| {float(r['packing_factor']):.2f}x "
                f"| {float(r['lr_padded']):.2e} "
                f"| {float(r['lr_packed']):.2e} "
                f"| {float(r['shift']):.2f}x "
                f"| {cell} "
                f"| {seed_note} |"
            )
    lines.append("")
    return "\n".join(lines)


def replace(text: str, tag: str, body: str, path: Path, required: bool = True) -> str:
    pattern = re.compile(f"<!--{tag}-->.*?<!--/{tag}-->", re.S)
    if not pattern.search(text):
        if not required:
            return text
        raise SystemExit(f"marker <!--{tag}--> not found in {path}; nothing changed")
    return pattern.sub(f"<!--{tag}-->{body}<!--/{tag}-->", text)


def main() -> None:
    total, timed, hours, notes = tally()
    split = f" ({', '.join(notes)})" if len(notes) > 1 else ""
    untimed = total - timed
    caveat = (
        f"; {untimed} row{'s' if untimed != 1 else ''} carr"
        f"{'y' if untimed != 1 else 'ies'} no wall time, having been harvested "
        f"from earlier runs of the same configs"
        if untimed else ""
    )
    compute = (f"about {hours:.0f} GPU-hours (measured across the {timed:,} runs "
               f"that recorded wall time{caveat})")
    table = exponent_table()

    for path in PAPERS:
        if not path.exists():
            continue
        text = path.read_text()
        # The status block, and so the run count, is a repository artefact and is
        # not in the workshop build; the other two spans are in appendices both
        # versions share.
        text = replace(text, "runs", f"{total:,} runs{split}", path, required=False)
        text = replace(text, "compute", compute, path)
        text = replace(text, "exponents", table, path)
        path.write_text(text)
        print(f"{total:,} runs, {timed:,} timed, {hours:.1f} GPU-hours -> {path}")


if __name__ == "__main__":
    main()
