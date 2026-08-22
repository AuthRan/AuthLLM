"""Regenerate the figures README.md embeds, from the logs in this tree.

Usage:
    python scripts/plot_results.py                  # every figure
    python scripts/plot_results.py --only chat-sweep dpo-reversal

Needs the optional plotting extra:
    pip install -e .[viz]

Every figure is built from a file a real run wrote -- logs/*.csv for the
curves, and the scored tables in README section 10 for the comparisons.
There is no synthetic data path here, which is the point: if a log is
missing the figure fails loudly rather than drawing something plausible.
"""

from __future__ import annotations

import argparse

from ashugpt.viz.figures import ALL
from ashugpt.viz.style import apply_style


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--only",
        nargs="+",
        choices=sorted(ALL),
        help="Build only these figures (default: all of them)",
    )
    args = parser.parse_args()

    apply_style()
    names = args.only or sorted(ALL)
    print(f"Building {len(names)} figure(s) into resources/plots/")
    for name in names:
        ALL[name]()
    print("done")


if __name__ == "__main__":
    main()
