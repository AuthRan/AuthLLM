"""Behavioural measurements over generated text.

Held-out loss says how well a model predicts text someone else wrote. It says
nothing about what the model does when it is the one writing, which is where
the differences between a base model, an instruction-tuned one and a chat model
are most obvious -- one runs to the token cap, one stops, one stops in the
right place.

These are the checks that read a generation and answer a yes/no question about
it. They live here rather than in the scripts that call them because
`scripts/eval_instruction_following.py` and `scripts/eval_chat.py` both need
the same answers, and two copies of "what counts as a loop" would eventually
disagree and make their tables incomparable.
"""

from __future__ import annotations


def has_repeated_window(token_ids: list[int], window: int = 10) -> bool:
    """True if any `window`-length run of tokens occurs more than once.

    The degeneration mode small models fall into is not repeating a word, it
    is re-emitting a whole clause: the next-token distribution is shallow, the
    sampler lands in an attractor, and the same phrasing comes back around.
    Ten tokens is long enough that ordinary English -- which repeats names,
    articles and short phrases constantly -- does not trip it, and short enough
    to catch a loop within a couple of hundred tokens.

    A generation shorter than two windows cannot repeat one and is never
    counted as looping.
    """
    if len(token_ids) < 2 * window:
        return False
    seen = set()
    for i in range(len(token_ids) - window + 1):
        key = tuple(token_ids[i : i + window])
        if key in seen:
            return True
        seen.add(key)
    return False
