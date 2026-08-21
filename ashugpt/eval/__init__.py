"""Evaluation: validation loop, held-out loss, perplexity, behavioural checks
over generated text, and -- for a preference run, whose loss is a ranking
rather than a prediction -- held-out ranking accuracy and reward margin.
"""

from ashugpt.eval.generation import has_repeated_window
from ashugpt.eval.perplexity import evaluate, perplexity_from_loss
from ashugpt.eval.preference import evaluate_preferences

__all__ = ["evaluate", "perplexity_from_loss", "evaluate_preferences", "has_repeated_window"]
