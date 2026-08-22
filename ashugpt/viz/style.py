"""One visual language for every figure in the README.

The figures land in a README that people read on GitHub in both light and
dark themes, so they are drawn on an explicit white ground rather than a
transparent one -- a transparent PNG renders as dark-on-dark for half the
audience, which is the most common way a good plot becomes unreadable.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # No display here; every figure is written to a file.

import matplotlib.pyplot as plt

# A qualitative ramp that survives greyscale printing and the common forms
# of colour blindness: distinct in hue *and* in lightness, so two adjacent
# series never rely on red-vs-green alone.
COLORS = {
    "pretrain": "#1f4e79",
    "stage1": "#2e7d32",
    "stage2": "#c9700a",
    "chat": "#7b3fa0",
    "dpo": "#b0264a",
    "baseline": "#6b7280",
    "accent": "#0f766e",
    "human": "#374151",
}

SERIES = [COLORS["pretrain"], COLORS["stage1"], COLORS["stage2"], COLORS["chat"], COLORS["dpo"]]

GRID = "#d8dde3"
TEXT = "#1f2430"
MUTED = "#5b6472"


def apply_style() -> None:
    """Set the rcParams every figure in this package shares."""
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT,
            "axes.titlecolor": TEXT,
            "axes.titleweight": "bold",
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.7,
            "grid.alpha": 0.9,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.frameon": False,
            "legend.fontsize": 9,
            "font.size": 10,
            "figure.dpi": 130,
            "savefig.dpi": 130,
            "savefig.bbox": "tight",
            "lines.linewidth": 1.8,
        }
    )


def annotate(ax, text: str, xy, xytext, color: str = TEXT) -> None:
    """A callout with a leader line, used to put the finding on the figure.

    A curve that needs a paragraph of README next to it to mean anything is
    a curve that will be screenshotted without the paragraph.
    """
    ax.annotate(
        text,
        xy=xy,
        xytext=xytext,
        fontsize=8.5,
        color=color,
        arrowprops={"arrowstyle": "->", "color": color, "linewidth": 1.0, "shrinkA": 0, "shrinkB": 3},
    )


def save(fig, path: str | Path) -> Path:
    """Write a figure and report where it went."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path}")
    return path
