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
PAPER = REPO / "paper" / "paper.md"
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
# are grouped in: largest model first, and the matched-budget control last
# because it is a control on 30M rather than a fourth size.
MODEL_ORDER = ["124M", "30M", "7M", "30M@19.7", "7M@18.0"]
MODEL_LABEL = {
    "124M": "124M",
    "30M": "30M",
    "7M": "7M",
    "30M@19.7": "30M @ 19.7 tok/param",
    "7M@18.0": "7M @ 18.0 tok/param",
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


def replace(text: str, tag: str, body: str) -> str:
    pattern = re.compile(f"<!--{tag}-->.*?<!--/{tag}-->", re.S)
    if not pattern.search(text):
        raise SystemExit(f"marker <!--{tag}--> not found in {PAPER}; nothing changed")
    return pattern.sub(f"<!--{tag}-->{body}<!--/{tag}-->", text)


def main() -> None:
    total, timed, hours, notes = tally()
    text = PAPER.read_text()
    split = f" ({', '.join(notes)})" if len(notes) > 1 else ""
    text = replace(text, "runs", f"{total:,} runs{split}")
    untimed = total - timed
    caveat = (
        f"; {untimed} row{'s' if untimed != 1 else ''} carr"
        f"{'y' if untimed != 1 else 'ies'} no wall time, having been harvested "
        f"from earlier runs of the same configs"
        if untimed else ""
    )
    text = replace(
        text, "compute",
        f"about {hours:.0f} GPU-hours (measured across the {timed:,} runs that "
        f"recorded wall time{caveat})",
    )
    text = replace(text, "exponents", exponent_table())
    PAPER.write_text(text)
    print(f"{total:,} runs, {timed:,} timed, {hours:.1f} GPU-hours -> {PAPER}")


if __name__ == "__main__":
    main()
