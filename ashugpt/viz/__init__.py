"""Plotting for the training logs and evaluation tables this repo produces.

Everything here reads files that a real run wrote -- `logs/*.csv` for the
curves, and the numbers in README.md section 10 for the scored comparisons.
Nothing is generated, smoothed into a nicer shape, or carried over from a
run that did not happen: if a figure cannot be built from a log in the
tree, it is not built.

Import-time note: matplotlib is an optional dependency (`pip install
-e .[viz]`), because nothing under ashugpt/ needs it to train, evaluate or
serve. It is imported inside this subpackage only.
"""

from ashugpt.viz.logs import TrainingLog, load_log
from ashugpt.viz.style import COLORS, apply_style, save

__all__ = ["TrainingLog", "load_log", "COLORS", "apply_style", "save"]
