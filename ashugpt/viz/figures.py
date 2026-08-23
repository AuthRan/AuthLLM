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
from ashugpt.viz.style import COLORS, MUTED, TEXT, annotate, apply_style, save

LOGS = Path("logs")
OUT = Path("resources/plots")


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


ALL = {
    "pretraining": pretraining_curve,
    "scaling": scaling_ladder,
    "behaviour": stage_behaviour,
    "chat-sweep": chat_sweep,
    "dpo-reversal": dpo_metric_reversal,
}
