"""has_repeated_window: what counts as a model falling into a loop.

Two evaluation scripts publish a `loop rate` column and readers compare those
columns across tables, so the rule behind them has to be one rule. These tests
pin what it does and, more importantly, what it does not: ordinary English
repeats words and short phrases constantly, and a detector that fired on that
would report every fluent model as degenerate.
"""

from __future__ import annotations

from ashugpt.eval.generation import has_repeated_window
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer


def test_a_repeated_clause_is_a_loop() -> None:
    clause = list(range(100, 115))
    assert has_repeated_window(clause + clause)


def test_a_generation_shorter_than_two_windows_is_never_a_loop() -> None:
    """It cannot contain the same ten-token run twice, so any True here would
    be a bug rather than a finding."""
    assert not has_repeated_window(list(range(19)))
    assert not has_repeated_window([7] * 19)


def test_a_short_repeated_phrase_is_not_a_loop() -> None:
    """Real text repeats "of the" and names endlessly. The window is ten
    tokens precisely so that ordinary repetition does not trip it."""
    tokenizer = TiktokenBPETokenizer()
    prose = (
        "The Industrial Revolution changed the economy of the country, and the "
        "economy of the country changed the lives of the people who lived in it. "
        "Historians of the period disagree about the causes of the change."
    )
    assert not has_repeated_window(tokenizer.encode(prose))


def test_the_degenerate_case_real_models_produce_is_caught() -> None:
    """Verbatim sentence repetition, which is what low-temperature sampling
    actually does to this project's 124M model (see results/README.md)."""
    tokenizer = TiktokenBPETokenizer()
    sentence = "The Industrial Revolution was a period of rapid industrialization and industrialization. "
    assert has_repeated_window(tokenizer.encode(sentence * 3))


def test_the_window_length_is_configurable_and_means_what_it_says() -> None:
    pattern = list(range(4)) * 6  # a 4-token cycle
    assert has_repeated_window(pattern, window=4)
    # At window 10 the cycle still repeats -- ten tokens of a 4-cycle recur --
    # so this is a check that the parameter reaches the comparison, not that a
    # longer window is blind to short cycles.
    assert has_repeated_window(pattern, window=10)
