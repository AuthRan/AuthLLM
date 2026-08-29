"""The figures README.md embeds.

Each function builds exactly one figure from data already in the tree --
a CSV a training run wrote, or a table in README section 10 that a scoring
script produced. The measured numbers that are not in a CSV are lifted
into module-level tables here, each one carrying the section it came from,
so a figure and its prose cannot drift apart silently.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from ashugpt.config import load_model_config
from ashugpt.viz.logs import load_log
from ashugpt.viz.style import (COLORS, MUTED, TEXT, annotate, apply_style,
                               paper_text, save)

LOGS = Path("logs")
OUT = Path("resources/plots")
SWEEPS = Path("results/lr_scaling_sweep.csv")


# --- Measured numbers that live in a table rather than a CSV -----------------
# Model presets. Only the status string is written here: the shape and the
# parameter count are read out of configs/model/*.yaml at draw time, by the
# same approx_param_count() README section 4 quotes, because they are
# derivable and a derivable number that is typed by hand is a number that
# drifts. This one had: the ladder drew tiny at 12.3M and small at 33.9M
# against configs saying 7,292,032 and 29,938,560.
CONFIGS = Path("configs/model")
PRESET_STATUS = [
    ("tiny", "trained on demo corpora"),
    ("small", "trained on demo corpora"),
    ("medium", "trained: 2.46B tokens"),
    ("xl_1b", "fits + steps, untrained"),
]


def _presets() -> list[tuple[str, int, int, int, str]]:
    """(name, layers, d_model, parameters, status) for each preset."""
    out = []
    for name, status in PRESET_STATUS:
        config = load_model_config(CONFIGS / f"{name}.yaml")
        out.append((name, config.n_layers, config.d_model, config.approx_param_count(), status))
    return out


PRESETS = _presets()

# Behaviour on the held-out Dolly split -- README sections 10.4, 10.7, 10.8.
# Same script, same settings, same split for every row, which is the only
# reason these are on one axis together.
#
# The last two are *branches*, not a chain: scripts/finetune.py --format chat
# and scripts/preference_tune.py both start from the same instruction-tuned
# checkpoint (sft_dolly_packed3e5/step_940.pt). Drawing them end to end would
# claim a four-stage pipeline this project never ran.
BEHAVIOUR = [
    ("base 124M", "pretrained only", 30, 80, 179, COLORS["pretrain"]),
    ("instruction-tuned", "Alpaca -> Dolly", 92, 20, 62, COLORS["stage1"]),
    ("chat branch", "-> UltraChat", 85, 30, 105, COLORS["chat"]),
    ("preference branch", "-> DPO on HH-RLHF", 98, 18, 70, COLORS["dpo"]),
]

# The chat learning-rate sweep -- README section 10.7.
CHAT_SWEEP = [
    ("1.5e-5", "sft_chat_lr15e6", 2.1187),
    ("4.0e-5", "sft_chat_lr4e5", 2.0630),
    ("1.0e-4", "sft_chat_lr1e4", 2.0333),
    ("2.5e-4", "sft_chat_lr25e4", 2.0545),
]

# The DPO sweep's two rankings -- README section 10.8. The whole point of
# this figure is that these two orderings are reverses of each other.
DPO_SWEEP = [
    ("1.0e-6\n(shipped)", 57.1, 55.2, 98, 18, 70),
    ("5.0e-6", 58.4, 56.1, 98, 22, 79),
    ("2.0e-5", 59.9, 56.6, 88, 40, 103),
]
SFT_BASELINE = (50.0, 54.3, 92, 20, 62)


def _thousands(value, _pos) -> str:
    return f"{value / 1000:.0f}k" if value >= 1000 else f"{value:.0f}"


def pretraining_curve() -> Path:
    """The 124M run: 20,000 steps, 2.46B tokens of FineWeb-Edu."""
    log = load_log(LOGS / "medium_metrics.csv", "medium (124M)")
    fig, (ax, ax_lr) = plt.subplots(
        2, 1, figsize=(9, 5.6), sharex=True, gridspec_kw={"height_ratios": [3, 1], "hspace": 0.12}
    )

    ax.plot(log.train_steps, log.train_loss, color=COLORS["pretrain"], alpha=0.22, linewidth=0.9)
    ax.plot(
        log.train_steps,
        log.smoothed_train_loss(window=40),
        color=COLORS["pretrain"],
        label="training loss (40-point trailing mean)",
    )
    ax.plot(
        log.val_steps,
        log.val_loss,
        color=COLORS["dpo"],
        marker="o",
        markersize=3.2,
        linewidth=1.6,
        label="held-out validation loss",
    )

    final_ppl = log.final_val_perplexity
    if final_ppl is not None:
        annotate(
            ax,
            f"final val loss {log.final_val_loss:.4f}\nperplexity {final_ppl:.2f}",
            xy=(log.val_steps[-1], log.val_loss[-1]),
            xytext=(log.max_step * 0.55, log.val_loss[-1] + 0.62),
            color=COLORS["dpo"],
        )
    # The first 1,500 steps compress everything after them into a flat line,
    # and everything after them is where the run was actually judged -- so the
    # tail gets its own axes rather than a log scale that hides the shape.
    inset = ax.inset_axes([0.36, 0.36, 0.60, 0.46])
    tail_from = 1500
    t_idx = [i for i, step in enumerate(log.train_steps) if step >= tail_from]
    v_idx = [i for i, step in enumerate(log.val_steps) if step >= tail_from]
    smoothed = log.smoothed_train_loss(window=40)
    inset.plot([log.train_steps[i] for i in t_idx], [smoothed[i] for i in t_idx], color=COLORS["pretrain"])
    inset.plot(
        [log.val_steps[i] for i in v_idx],
        [log.val_loss[i] for i in v_idx],
        color=COLORS["dpo"],
        marker="o",
        markersize=2.8,
        linewidth=1.4,
    )
    inset.set_title(f"steps {tail_from:,}-{log.max_step:,}, where the run was judged", fontsize=8, color=MUTED)
    inset.tick_params(labelsize=7.5)
    inset.xaxis.set_major_formatter(FuncFormatter(_thousands))
    inset.set_facecolor("#fbfcfd")
    inset.text(
        0.97, 0.88,
        "no divergence, no restarts,\nno loss spikes across ~27 hours",
        transform=inset.transAxes, ha="right", va="top", fontsize=7.5, color=MUTED,
    )

    ax.set_ylabel("cross-entropy loss (nats)")
    ax.set_title("Pretraining the 124M model from random init — FineWeb-Edu, 2.46B tokens")
    ax.legend(loc="lower left", bbox_to_anchor=(0.005, 0.02))
    ax.set_ylim(2.8, 9.9)

    ax_lr.plot(log.train_steps, log.lr, color=COLORS["accent"])
    ax_lr.set_ylabel("learning rate")
    ax_lr.set_xlabel("optimizer step")
    ax_lr.xaxis.set_major_formatter(FuncFormatter(_thousands))
    ax_lr.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
    ax_lr.text(
        log.max_step * 0.02,
        max(log.lr) * 0.30,
        "warmup, then cosine decay",
        fontsize=8.5,
        color=MUTED,
    )
    return save(fig, OUT / "01-pretraining-124m.png")


def scaling_ladder() -> Path:
    """What was built at each size, and what was actually trained."""
    fig, ax = plt.subplots(figsize=(9, 4.0))
    names = [p[0] for p in PRESETS]
    params = [p[3] for p in PRESETS]
    colors = [COLORS["baseline"], COLORS["baseline"], COLORS["pretrain"], COLORS["baseline"]]
    bars = ax.bar(names, params, color=colors, width=0.58)
    bars[2].set_edgecolor(COLORS["dpo"])
    bars[2].set_linewidth(2.0)

    ax.set_yscale("log")
    ax.set_ylabel("parameters (log scale)")
    ax.set_title("Four presets, one codebase — and which of them a real run has trained", pad=26)
    for bar, preset in zip(bars, PRESETS):
        _name, layers, d_model, count, _status = preset
        label = f"{count / 1e6:.1f}M" if count < 1e9 else f"{count / 1e9:.2f}B"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            count * 1.5,
            f"{label}  ·  {layers}L x {d_model}d",
            ha="center",
            fontsize=8.8,
            color=TEXT,
        )
    ax.set_xticks(range(len(PRESETS)))
    ax.set_xticklabels([f"{p[0]}\n{p[4]}" for p in PRESETS], fontsize=9)
    ax.set_ylim(3e6, 1.2e10)
    ax.text(
        0.5,
        1.02,
        "same model code for all four — only the config changes",
        transform=ax.transAxes,
        ha="center",
        fontsize=8.5,
        color=MUTED,
    )
    return save(fig, OUT / "02-scaling-ladder.png")


def stage_behaviour() -> Path:
    """What each fine-tuning stage did to behaviour, on one held-out split."""
    fig, axes = plt.subplots(1, 3, figsize=(11.4, 4.6))
    names = [b[0] for b in BEHAVIOUR]
    colors = [b[5] for b in BEHAVIOUR]

    panels = [
        ("stop rate (%)", [b[2] for b in BEHAVIOUR], "higher is better", True),
        ("loop rate (%)", [b[3] for b in BEHAVIOUR], "lower is better", False),
        ("mean answer length (tokens)", [b[4] for b in BEHAVIOUR], "Dolly's own answers are short", False),
    ]
    for ax, (title, values, note, is_pct) in zip(axes, panels):
        bars = ax.bar(range(len(values)), values, color=colors, width=0.62)
        # The two branches are drawn hatched so the figure cannot be read as
        # a four-step chain -- they share a parent, they do not follow it.
        for bar in bars[2:]:
            bar.set_hatch("//")
            bar.set_edgecolor("white")
        ax.set_title(title, fontsize=10.5, pad=16)
        ax.set_xticks(range(len(values)))
        ax.set_xticklabels(names, rotation=24, ha="right", fontsize=8.2)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + (max(values) * 0.035),
                f"{value}",
                ha="center",
                fontsize=8.8,
                color=TEXT,
            )
        ax.set_ylim(0, 116 if is_pct else max(values) * 1.30)
        ax.text(0.02, 0.965, note, transform=ax.transAxes, fontsize=8.2, color=MUTED, va="top")

    fig.suptitle(
        "Every stage, scored on the same held-out Dolly split by the same script",
        fontsize=12,
        fontweight="bold",
        y=1.0,
    )
    fig.subplots_adjust(bottom=0.26)
    fig.text(
        0.5,
        0.03,
        "hatched = branches: chat and DPO both start from the instruction-tuned checkpoint, "
        "so they are siblings rather than later steps",
        ha="center",
        fontsize=8.4,
        color=MUTED,
    )
    return save(fig, OUT / "03-stage-behaviour.png")


def chat_sweep() -> Path:
    """The chat learning-rate sweep, and the epoch that shipped."""
    fig, (ax, ax_bar) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [1.5, 1]})

    ramp = [COLORS["baseline"], COLORS["stage1"], COLORS["chat"], COLORS["stage2"]]
    for (label, stem, _final), color in zip(CHAT_SWEEP, ramp):
        log = load_log(LOGS / f"{stem}.csv", label)
        ax.plot(log.val_steps, log.val_loss, marker="o", markersize=3.4, color=color, label=f"max_lr {label}")

    epoch = load_log(LOGS / "sft_chat.csv", "one epoch")
    ax.plot(
        epoch.val_steps,
        epoch.val_loss,
        marker="o",
        markersize=3.4,
        color=COLORS["dpo"],
        linewidth=2.2,
        label="1.0e-4, full epoch (shipped)",
    )
    annotate(
        ax,
        f"still falling at the\nlast eval — {epoch.final_val_loss:.4f}",
        xy=(epoch.val_steps[-1], epoch.val_loss[-1]),
        xytext=(epoch.max_step * 0.36, 1.968),
        color=COLORS["dpo"],
    )
    ax.set_xlabel("optimizer step")
    ax.set_ylabel("held-out chat loss (nats)")
    ax.set_title("Chat stage: four learning rates, then the epoch")
    ax.legend(loc="upper right")

    labels = [c[0] for c in CHAT_SWEEP]
    finals = [c[2] for c in CHAT_SWEEP]
    bars = ax_bar.bar(labels, finals, color=ramp, width=0.6)
    best = finals.index(min(finals))
    bars[best].set_edgecolor(COLORS["dpo"])
    bars[best].set_linewidth(2.2)
    for bar, value in zip(bars, finals):
        ax_bar.text(
            bar.get_x() + bar.get_width() / 2, value + 0.006, f"{value:.4f}", ha="center", fontsize=8.5, color=TEXT
        )
    ax_bar.set_ylim(min(finals) - 0.04, max(finals) + 0.04)
    ax_bar.set_ylabel("held-out loss @ 300 steps")
    ax_bar.set_title("A bracketed minimum, not an edge")
    ax_bar.set_xlabel("max_lr\n1.0e-4 wins, and 2.5e-4 is past it", fontsize=9, labelpad=4)
    return save(fig, OUT / "04-chat-lr-sweep.png")


def dpo_metric_reversal() -> Path:
    """This project's recurring finding, in one picture."""
    fig, (ax_pref, ax_behav) = plt.subplots(1, 2, figsize=(11, 4.3))
    labels = [d[0] for d in DPO_SWEEP]
    x = range(len(labels))
    ramp = [COLORS["stage1"], COLORS["stage2"], COLORS["dpo"]]

    ax_pref.bar(x, [d[1] for d in DPO_SWEEP], color=ramp, width=0.58)
    ax_pref.axhline(
        SFT_BASELINE[0], color=MUTED, linestyle="--", linewidth=1.2, label="the model it started from"
    )
    ax_pref.legend(loc="lower right")
    for i, d in enumerate(DPO_SWEEP):
        ax_pref.text(i, d[1] + 0.35, f"{d[1]}%", ha="center", fontsize=9, color=TEXT)
    ax_pref.set_xticks(list(x))
    ax_pref.set_xticklabels(labels, fontsize=8.5)
    ax_pref.set_ylim(46, 63)
    ax_pref.set_ylabel("DPO accuracy (%)")
    ax_pref.set_title("Ranked by the objective it trained on")
    ax_pref.set_xlabel("more learning rate looks strictly better", fontsize=8.5, color=MUTED, labelpad=8)

    width = 0.27
    stop = [d[3] for d in DPO_SWEEP]
    loop = [d[4] for d in DPO_SWEEP]
    length = [d[5] for d in DPO_SWEEP]
    ax_behav.bar([i - width for i in x], stop, width=width, color=COLORS["stage1"], label="stop rate %")
    ax_behav.bar(list(x), loop, width=width, color=COLORS["dpo"], label="loop rate %")
    ax_behav.bar([i + width for i in x], length, width=width, color=COLORS["baseline"], label="mean tokens")
    for ref, color in ((SFT_BASELINE[2], COLORS["stage1"]), (SFT_BASELINE[3], COLORS["dpo"]), (SFT_BASELINE[4], COLORS["baseline"])):
        ax_behav.axhline(ref, color=color, linestyle=":", linewidth=1.3, alpha=0.85)
    ax_behav.set_xticks(list(x))
    ax_behav.set_xticklabels(labels, fontsize=8.5)
    ax_behav.set_title("Ranked by how the model actually behaves")
    ax_behav.legend(loc="upper left", ncol=1)
    ax_behav.set_ylim(0, 125)
    ax_behav.set_xlabel(
        "dotted lines = the model it started from; the order reverses",
        fontsize=8.5, color=MUTED, labelpad=8,
    )

    fig.suptitle(
        "The same three DPO checkpoints, ranked twice — the finding this project keeps re-learning",
        fontsize=12, fontweight="bold", y=1.02,
    )
    return save(fig, OUT / "05-dpo-metric-reversal.png")


def lr_scaling() -> Path:
    """What packing does to the optimal learning rate, on both corpora.

    Read from results/lr_scaling_sweep.csv rather than a table, because this
    figure exists to show the shape of four curves and their minima, and a
    minimum copied by hand is a minimum that drifts.

    Seed 1337 only. The fine-tune shuffles its held-out split with the same
    seed it trains with, so curves from different seeds sit at different
    offsets and cannot share an axis. Optima aggregated over seeds live in the
    paper's tables; this is one seed's curves, honestly labelled.
    """
    import csv
    import math

    by_cell: dict[tuple[str, str], list[tuple[float, float]]] = {}
    with SWEEPS.open() as handle:
        for row in csv.DictReader(handle):
            if int(row["seed"]) != 1337 or not row["final_val_loss"]:
                continue
            key = (row.get("dataset") or "alpaca", row["cell"])
            by_cell.setdefault(key, []).append((float(row["max_lr"]), float(row["final_val_loss"])))

    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4))
    panels = [
        ("alpaca", "Alpaca — packing factor 4.47x", ("unpacked_350", "unpacked_1600", "packed_350", "packed_1600")),
        ("dolly", "Dolly — packing factor 2.92x", ("unpacked_136", "unpacked_430", "packed_136", "packed_430")),
    ]
    ramp = [COLORS["baseline"], COLORS["pretrain"], COLORS["stage2"], COLORS["dpo"]]

    for ax, (dataset, title, cells) in zip(axes, panels):
        for cell, color in zip(cells, ramp):
            points = sorted(by_cell.get((dataset, cell), []))
            if not points:
                continue
            xs = [lr for lr, _ in points]
            ys = [loss for _, loss in points]
            packed = cell.startswith("packed")
            ax.plot(
                xs, ys,
                marker="o", markersize=3.6, color=color,
                linestyle="-" if packed else "--",
                label=f"{'packed' if packed else 'padded'}, {cell.rsplit('_', 1)[1]} steps",
            )
            best = min(range(len(ys)), key=lambda i: ys[i])
            ax.plot([xs[best]], [ys[best]], marker="v", markersize=8, color=color, linestyle="none")

        ax.set_xscale("log")
        ax.set_xlabel("peak learning rate")
        ax.set_title(title)
        ax.legend(loc="upper left", fontsize=8.5)

    axes[0].set_ylabel("held-out loss (nats), seed 1337")
    # Headroom above the curves so the legend and the annotation below it each
    # get clear space; without it the two collide in the upper left.
    low, high = axes[0].get_ylim()
    axes[0].set_ylim(low, high + 0.30 * (high - low))
    # The two curves that matter are one packed epoch against one padded epoch:
    # same data budget, optima a factor of ~5 apart. The ratio quoted is this
    # seed's (4.92x); the paper's tables carry the seed-aggregated 4.86x.
    annotate(
        axes[0],
        "one packed epoch's optimum —\n4.9x above the padded epoch's\nat this seed, same data budget",
        xy=(1.37e-4, 2.0175),
        xytext=(1.15e-5, 2.27),
        color=COLORS["stage2"],
    )
    return save(fig, OUT / "06-lr-scaling-packing.png")


def lr_scaling_control() -> Path:
    """The same batch, assembled two ways: does the optimum care which?

    Packing raises supervised tokens per step and leaves the number of
    forward-pass rows alone. Raising gradient accumulation instead reaches the
    same tokens per step through ~4.5x as many rows. Matched that way the two
    cells agree on everything a batch-size rule could be a function of, so if
    the optimum is set by the statistical batch the two curves share a minimum
    -- and if it is set by rows, they sit a factor of 4.5 apart.

    Two series, so identity is carried by line style and marker as well as
    colour: solid circles packed, dashed squares padded, the same assignment the
    factorial figure uses. Seed 1337, the seed both cells share.
    """
    import csv
    import math

    by_cell: dict[tuple[str, str], list[tuple[float, float]]] = {}
    with SWEEPS.open() as handle:
        for row in csv.DictReader(handle):
            if int(row["seed"]) != 1337 or not row["final_val_loss"]:
                continue
            key = (row.get("dataset") or "alpaca", row["cell"])
            by_cell.setdefault(key, []).append((float(row["max_lr"]), float(row["final_val_loss"])))

    def optimum(points: list[tuple[float, float]]) -> float | None:
        """Parabola through the argmin and its neighbours, in log(lr).

        The same estimator as scripts/analyze_lr_scaling.py, which is canonical;
        kept here so the viz package does not import from scripts/.
        """
        points = sorted(points)
        index = min(range(len(points)), key=lambda i: points[i][1])
        if index == 0 or index == len(points) - 1:
            return None
        (x1, y1), (x2, y2), (x3, y3) = (
            (math.log(points[i][0]), points[i][1]) for i in (index - 1, index, index + 1)
        )
        denominator = (x1 - x2) * (x1 - x3) * (x2 - x3)
        a = (x3 * (y2 - y1) + x2 * (y1 - y3) + x1 * (y3 - y2)) / denominator
        b = (x3 * x3 * (y1 - y2) + x2 * x2 * (y3 - y1) + x1 * x1 * (y2 - y3)) / denominator
        return math.exp(-b / (2 * a)) if a > 0 else None

    panels = [
        ("alpaca", "Alpaca", "packed_350", "wide_350", 8444, 8496, 32, 144),
        ("dolly", "Dolly", "packed_136", "wide_136", 6632, 6816, 32, 96),
    ]
    # Drawn large and typeset small; see style.paper_text().
    with paper_text():
        fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4))

        for ax, (dataset, title, packed_cell, wide_cell, pk_tok, wd_tok, pk_rows, wd_rows) in zip(axes, panels):
            series = [
                (packed_cell, "packed", COLORS["stage2"], "-", "o"),
                (wide_cell, "padded, wide batch", COLORS["pretrain"], "--", "s"),
            ]
            marks = []
            for cell, label, color, style, marker in series:
                points = sorted(by_cell.get((dataset, cell), []))
                if not points:
                    continue
                ax.plot([lr for lr, _ in points], [loss for _, loss in points],
                        marker=marker, markersize=5, linewidth=2, color=color,
                        linestyle=style, label=label)
                best = optimum(points)
                if best is not None:
                    ax.axvline(best, color=color, linestyle=":", linewidth=1.4, alpha=0.75)
                    marks.append((label, best))

            ax.set_xscale("log")
            ax.set_xlabel("peak learning rate")
            # The counts are in the caption and in Appendix K: spelled out here
            # they make a title wider than its own panel at paper type size.
            ax.set_title(f"{title} — matched to {abs(wd_tok - pk_tok) / pk_tok:.1%}")
            ax.legend(loc="upper left", fontsize=8.5)

            if len(marks) == 2:
                # Headroom first: on Dolly the padded arm climbs into the top-right
                # corner, so the corner has to be made empty before anything is put
                # in it.
                low, high = ax.get_ylim()
                ax.set_ylim(low, high + 0.22 * (high - low))
                # Upper right: both curves fall away from the top-left, so this
                # corner is the one reliably empty in either panel.
                ratio = marks[1][1] / marks[0][1]
                ax.text(0.97, 0.95,
                        f"optima {ratio:.2f}x apart\n{pk_rows} rows/step vs {wd_rows}",
                        transform=ax.transAxes, ha="right", va="top", fontsize=9,
                        color=TEXT, linespacing=1.6)

        axes[0].set_ylabel("held-out loss (nats)")
        return save(fig, OUT / "07-lr-scaling-control.png")


def lr_scaling_regime() -> Path:
    """What the packing exponent depends on, and what it does not.

    Left: the exponent against the size of the run, with everything else held --
    packing factor near 4.5x, padded batch near 1,850 supervised tokens, the same
    length distribution. Right: the exponent against the packing factor across
    Alpaca's three length terciles, which hold the size of the run fixed instead.
    The first axis moves it; the second does not, once the seed spread is drawn.

    Error bars are the seed bound from results/exponents.csv: both cells' max/min
    ranges carried through the ratio. They are ranges rather than standard
    errors, so they are conservative, and the right-hand panel is the reason to
    draw them at all -- without them its three points look like a trend.

    Reads the exported table rather than recomputing, so this cannot disagree
    with the paper about what an optimum is.
    """
    import csv

    rows = []
    path = OUT.parent.parent / "results" / "exponents.csv"
    if path.exists():
        with path.open() as handle:
            rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("run scripts/export_exponents.py first")

    SCALE = {"alpaca": "alpaca", "alpaca_third": "alpaca", "alpaca_ninth": "alpaca",
             "dolly": "dolly", "dolly_third": "dolly"}
    TERCILES = ("alpaca_long", "alpaca_mid", "alpaca_short")
    style = {
        ("124M", "alpaca"): (COLORS["pretrain"], "o", "Alpaca, 124M"),
        ("30M", "alpaca"): (COLORS["stage2"], "s", "Alpaca, 30M"),
        ("7M", "alpaca"): (COLORS["stage1"], "D", "Alpaca, 7M"),
        ("124M", "dolly"): (COLORS["dpo"], "^", "Dolly, 124M"),
        ("30M", "dolly"): (COLORS["chat"], "v", "Dolly, 30M"),
    }

    # Drawn large and typeset small; see style.paper_text().
    with paper_text():
        fig, (left, right) = plt.subplots(1, 2, figsize=(11.6, 4.4))

        series: dict[tuple[str, str], list] = {}
        for r in rows:
            key = (r["model"], SCALE.get(r["dataset"]))
            if key[1] is None or not r["train_examples"]:
                continue
            series.setdefault(key, []).append(
                (int(r["train_examples"]), float(r["exponent"]), float(r["bound"])))
        for key, pts in sorted(series.items()):
            if key not in style:
                continue
            color, marker, label = style[key]
            pts.sort()
            left.errorbar([p[0] for p in pts], [p[1] for p in pts], yerr=[p[2] for p in pts],
                          color=color, marker=marker, markersize=6, linewidth=2,
                          capsize=3, elinewidth=1.2, label=label)
        left.set_xscale("log")
        left.set_xlabel("training examples (one epoch each)")
        left.set_ylabel("exponent against the packing factor")
        left.set_title("Scale moves the exponent")
        left.legend(loc="upper left", fontsize=8.5)

        tercile = sorted((float(r["packing_factor"]), float(r["exponent"]), float(r["bound"]))
                         for r in rows if r["model"] == "124M" and r["dataset"] in TERCILES)
        if tercile:
            right.errorbar([p[0] for p in tercile], [p[1] for p in tercile],
                           yerr=[p[2] for p in tercile], color=COLORS["pretrain"],
                           marker="o", markersize=6, linewidth=2, capsize=3, elinewidth=1.2,
                           label="Alpaca length terciles, 124M")
        right.set_xscale("log")
        right.set_xlabel("packing factor (supervised tokens/step)")
        right.set_title("The packing ratio does not")
        right.legend(loc="upper right", fontsize=8.5)
        right.text(0.03, 0.04, "all three hold the size of the run fixed\nat 16,956 examples and 530 padded steps",
                   transform=right.transAxes, fontsize=8.5, color=MUTED, linespacing=1.5)

        # Plain tick labels: matplotlib's log default renders these as 6x10^3 and
        # 3x10^0, which is unreadable for quantities a reader wants to compare.
        from matplotlib.ticker import FixedFormatter, FixedLocator
        left.xaxis.set_major_locator(FixedLocator([5000, 10000, 20000, 50000]))
        left.xaxis.set_major_formatter(FixedFormatter(["5k", "10k", "20k", "50k"]))
        left.xaxis.set_minor_locator(FixedLocator([]))
        if tercile:
            factors = [p[0] for p in tercile]
            right.xaxis.set_major_locator(FixedLocator(factors))
            right.xaxis.set_major_formatter(FixedFormatter([f"{f:.1f}x" for f in factors]))
            right.xaxis.set_minor_locator(FixedLocator([]))

        # The two rules a reader will have in mind, drawn once so the panels share
        # them. Labelled on the left, where neither panel's data goes.
        for ax in (left, right):
            for value, name in ((0.5, "square-root"), (1.0, "linear")):
                ax.axhline(value, color=MUTED, linestyle=":", linewidth=1, alpha=0.6)
                ax.text(0.015, value, name, transform=ax.get_yaxis_transform(),
                        ha="left", va="bottom", fontsize=8, color=MUTED)
        lo = min(p[1] - p[2] for pts in series.values() for p in pts)
        hi = max(p[1] + p[2] for pts in series.values() for p in pts)
        for ax in (left, right):
            ax.set_ylim(min(lo, 0.2) - 0.08, max(hi, 1.1) + 0.16)

        return save(fig, OUT / "08-lr-scaling-regime.png")



# Which pretraining checkpoint each row of results/exponents.csv was fine-tuned
# from, as (metrics log, step). The perplexities themselves are NOT written
# here: they are read out of the log at draw time, because they are recorded and
# a recorded number that is typed by hand is a number that drifts. Only the
# checkpoint identity is structural enough to state.
BASE_CHECKPOINT = {
    "124M": ("medium_metrics.csv", None),      # None = the final validated step
    "124M@ppl39": ("medium_metrics.csv", 2500),
    "124M@ppl107": ("medium_metrics.csv", 500),
    "30M": ("small.csv", None),
    "30M@19.7": ("small.csv", 9000),
    "7M": ("mini.csv", None),
    "7M@18.0": ("mini.csv", 2000),
}

# Which model each row belongs to, and how to draw that family.
FAMILY = {
    "124M": ("124M", COLORS["pretrain"], "o"),
    "124M@ppl39": ("124M", COLORS["pretrain"], "o"),
    "124M@ppl107": ("124M", COLORS["pretrain"], "o"),
    "30M": ("30M", COLORS["stage2"], "s"),
    "30M@19.7": ("30M", COLORS["stage2"], "s"),
    "7M": ("7M", COLORS["stage1"], "D"),
    "7M@18.0": ("7M", COLORS["stage1"], "D"),
}


def _base_perplexity(log_name: str, step: int | None) -> float:
    """Validation perplexity of one pretraining checkpoint, from its own log."""
    import csv

    rows = [r for r in csv.DictReader((LOGS / log_name).open())
            if r.get("val_perplexity")]
    if step is None:
        return float(rows[-1]["val_perplexity"])
    for r in rows:
        if int(float(r["step"])) == step:
            return float(r["val_perplexity"])
    raise SystemExit(f"{log_name} has no validated step {step}")


def lr_scaling_quality() -> Path:
    """Base-model quality moves where the optimum is, not how far packing moves it.

    Left: the exponent against the base model's validation perplexity. The 124M
    series spans a 4.6x range of perplexity -- the converged model, an early
    checkpoint at the 30M model's quality, and one at the 7M model's -- and stays
    flat. The 7M model sits at the same perplexity as the last of those and more
    than half an exponent above it, which is the whole of sections 4.7.1 and
    4.7.2 in one picture: at matched quality the exponent follows the parameter
    count.

    Right: the optima themselves, on the same axis. Both arms rise together as
    the base model gets worse, by three to four times over this range, while the
    vertical distance between them -- the only quantity the paper measures --
    is left where it was. A practitioner can transfer the distance and not the
    level.

    Exponents and optima come from results/exponents.csv and perplexities from
    the pretraining logs, so nothing on either axis is typed by hand.
    """
    import csv

    path = OUT.parent.parent / "results" / "exponents.csv"
    if not path.exists():
        raise SystemExit("run scripts/export_exponents.py first")
    rows = [r for r in csv.DictReader(path.open())
            if r["dataset"] == "alpaca" and r["model"] in BASE_CHECKPOINT]
    if not rows:
        raise SystemExit("no whole-Alpaca rows in results/exponents.csv")

    points = []
    for r in rows:
        log_name, step = BASE_CHECKPOINT[r["model"]]
        family, color, marker = FAMILY[r["model"]]
        points.append({
            "family": family, "color": color, "marker": marker,
            "ppl": _base_perplexity(log_name, step),
            "exponent": float(r["exponent"]), "bound": float(r["bound"]),
            "padded": float(r["lr_padded"]), "packed": float(r["lr_packed"]),
        })

    # Drawn large and typeset small; see style.paper_text().
    with paper_text():
        fig, (left, right) = plt.subplots(1, 2, figsize=(11.6, 4.4))

        for family in ("124M", "30M", "7M"):
            pts = sorted((p for p in points if p["family"] == family),
                         key=lambda p: p["ppl"])
            if not pts:
                continue
            color, marker = pts[0]["color"], pts[0]["marker"]
            left.errorbar([p["ppl"] for p in pts], [p["exponent"] for p in pts],
                          yerr=[p["bound"] for p in pts], color=color, marker=marker,
                          markersize=6, linewidth=2, capsize=3, elinewidth=1.2,
                          label=f"{family} base model")
            for arm, style in (("padded", "--"), ("packed", "-")):
                right.plot([p["ppl"] for p in pts], [p[arm] for p in pts],
                           color=color, marker=marker, markersize=5, linewidth=1.8,
                           linestyle=style, label=f"{family}, {arm}")

        # A readable ladder rather than one tick per point: the measured
        # perplexities include 107, 115 and 142, whose labels collide at this width.
        ticks = [20, 30, 40, 60, 80, 100, 150]
        for ax in (left, right):
            ax.set_xscale("log")
            ax.set_xlabel("base-model validation perplexity")
            from matplotlib.ticker import FixedFormatter, FixedLocator
            ax.xaxis.set_major_locator(FixedLocator(ticks))
            ax.xaxis.set_major_formatter(FixedFormatter([str(t) for t in ticks]))
            ax.xaxis.set_minor_locator(FixedLocator([]))

        left.set_ylabel("exponent against the packing factor")
        left.set_title("Quality does not move the exponent")
        left.legend(loc="upper left", fontsize=8.5)
        for value, name in ((0.5, "square-root"), (1.0, "linear")):
            left.axhline(value, color=MUTED, linestyle=":", linewidth=1, alpha=0.6)
            left.text(0.015, value, name, transform=left.get_yaxis_transform(),
                      ha="left", va="bottom", fontsize=8, color=MUTED)

        # The comparison the figure exists for: two base models of the same quality
        # and 17x apart in parameters, which is the control section 4.7.2 registered.
        worst_124 = max((p for p in points if p["family"] == "124M"), key=lambda p: p["ppl"])
        small_7m = min((p for p in points if p["family"] == "7M"), key=lambda p: p["ppl"])
        left.annotate(
            "", xy=(small_7m["ppl"], small_7m["exponent"]),
            xytext=(worst_124["ppl"], worst_124["exponent"]),
            arrowprops={"arrowstyle": "<->", "color": COLORS["dpo"], "linewidth": 1.4})
        left.text(worst_124["ppl"] * 0.93,
                  (small_7m["exponent"] + worst_124["exponent"]) / 2,
                  "same perplexity,\n17x the parameters", fontsize=8.5,
                  color=COLORS["dpo"], linespacing=1.4, va="center", ha="right")

        right.set_yscale("log")
        right.set_ylabel("optimal peak learning rate")
        right.set_title("It moves both optima, together")
        right.legend(loc="upper left", fontsize=7.5, ncol=3)
        right.text(0.97, 0.04,
                   "both arms rise together;\nthe gap between them is what this paper measures",
                   transform=right.transAxes, fontsize=8.5, color=MUTED,
                   linespacing=1.5, ha="right")

        return save(fig, OUT / "09-lr-scaling-quality.png")



# How each corpus reads on an axis label. The exponent export writes the slug.
CORPUS_LABEL = {
    "alpaca": "Alpaca whole", "alpaca_third": "Alpaca third",
    "alpaca_ninth": "Alpaca ninth", "alpaca_short": "Alpaca short tercile",
    "alpaca_mid": "Alpaca middle tercile", "alpaca_long": "Alpaca long tercile",
    "dolly": "Dolly whole", "dolly_third": "Dolly third",
}

# The three base models that are settings rather than controls. The `@` rows of
# results/exponents.csv hold a size fixed and vary the pretraining budget or the
# base model's quality, so they are the same setting measured again and would
# double-count in a figure about how many settings a rule covers.
SETTING_MODELS = ("124M", "30M", "7M")


def lr_scaling_bracket() -> Path:
    """Every setting against the bracket an earlier draft of this paper proposed.

    That bracket was `[lr_pad * sqrt(p), lr_pad * 1.2p]`, which in exponent terms
    is 0.5 at the floor and log(1.2p)/log(p) at the ceiling -- about 1.12 to 1.17
    over the packing factors here. It held on the five settings it was drawn from
    and fails on five of the thirteen now measured, and the failures are not
    scattered: the three below the floor are the three smallest-scale settings,
    where the exponent is under the 0.5 that sqrt(p) assumes, and the two above
    the ceiling are the two smaller models on the largest corpus.

    Drawn because the table it replaces reports the five failures without showing
    that a fixed bracket cannot work: the measured exponents span 0.385 to 1.695,
    and no interval of the shape `[sqrt(p), c*p]` covers that at any single c.
    The dashed verticals are the range section 6 recommends instead.

    Reads results/exponents.csv, so it cannot disagree with the paper about what
    an optimum is.
    """
    import csv
    import math

    path = OUT.parent.parent / "results" / "exponents.csv"
    if not path.exists():
        raise SystemExit("run scripts/export_exponents.py first")
    rows = [r for r in csv.DictReader(path.open()) if r["model"] in SETTING_MODELS]
    if not rows:
        raise SystemExit("no setting rows in results/exponents.csv")

    items = []
    for r in rows:
        p_factor = float(r["packing_factor"])
        ceiling = math.log(1.2 * p_factor) / math.log(p_factor)
        exponent, bound = float(r["exponent"]), float(r["bound"])
        items.append({
            "label": f"{r['model']}  {CORPUS_LABEL.get(r['dataset'], r['dataset'])}",
            "exponent": exponent, "bound": bound,
            "floor": 0.5, "ceiling": ceiling,
            "misses": exponent < 0.5 or exponent > ceiling,
        })
    items.sort(key=lambda d: d["exponent"])

    # Wide and short on purpose: at \linewidth in a 5.5in NeurIPS column a
    # taller aspect costs two-thirds of a page, and thirteen rows do not need it.
    # Drawn large and typeset small; see style.paper_text().
    with paper_text():
        fig, ax = plt.subplots(figsize=(10.4, 4.9))
        y = list(range(len(items)))

        for i, d in enumerate(items):
            ax.plot([d["floor"], d["ceiling"]], [i, i], color=MUTED, linewidth=7,
                    alpha=0.22, solid_capstyle="butt", zorder=1)
        # One legend entry for the band, drawn off the visible rows.
        ax.plot([], [], color=MUTED, linewidth=7, alpha=0.22,
                label=r"the bracket that was proposed: $[\sqrt{p},\ 1.2p]$")

        for i, d in enumerate(items):
            color = COLORS["dpo"] if d["misses"] else COLORS["pretrain"]
            ax.errorbar(d["exponent"], i, xerr=d["bound"], color=color, marker="o",
                        markersize=6, capsize=3, elinewidth=1.2, linestyle="none",
                        zorder=3)
        ax.plot([], [], color=COLORS["pretrain"], marker="o", linestyle="none",
                label="measured, inside the bracket")
        ax.plot([], [], color=COLORS["dpo"], marker="o", linestyle="none",
                label="measured, outside it")

        for value in (0.4, 1.7):
            ax.axvline(value, color=COLORS["accent"], linestyle="--", linewidth=1.3,
                       alpha=0.85, zorder=2)
        ax.plot([], [], color=COLORS["accent"], linestyle="--",
                label=r"the range section 6 recommends: $p^{0.4}$ to $p^{1.7}$")

        ax.set_yticks(y)
        ax.set_yticklabels([d["label"] for d in items], fontsize=8.5)
        ax.set_ylim(-0.8, len(items) - 0.2)
        ax.set_xlabel("exponent against the packing factor")
        ax.set_title("A bracket fixed in $p$ cannot cover thirteen settings")
        ax.legend(loc="lower right", fontsize=8.5)
        ax.grid(axis="y", alpha=0.35)

        missed = sum(1 for d in items if d["misses"])
        # Boxed: it sits over the topmost bracket band, which is the row it is
        # describing and the one a reader looks at first.
        ax.text(0.02, 0.97, f"{missed} of {len(items)} settings fall outside",
                transform=ax.transAxes, fontsize=9, color=COLORS["dpo"], va="top",
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 2.5})

        return save(fig, OUT / "10-lr-scaling-bracket.png")


ALL = {
    "pretraining": pretraining_curve,
    "scaling": scaling_ladder,
    "behaviour": stage_behaviour,
    "chat-sweep": chat_sweep,
    "dpo-reversal": dpo_metric_reversal,
    "lr-scaling": lr_scaling,
    "lr-scaling-control": lr_scaling_control,
    "lr-scaling-regime": lr_scaling_regime,
    "lr-scaling-quality": lr_scaling_quality,
    "lr-scaling-bracket": lr_scaling_bracket,
}
