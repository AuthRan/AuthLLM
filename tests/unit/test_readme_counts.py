"""The test count the README advertises has to be the test count that exists.

The README states it twice -- in the summary table at the top and in the
fold over section 17 -- and both said 437 the moment the suite reached 438.
That is a small wrong number, and this repo has spent several commits on the
principle that a number nobody checks is a number that drifts: the figures
disagreed with the tables they were drawn from, a reproduction command named
a checkpoint its own config could not produce, and section 4 claimed the
124M model had never been trained.

A count is the easiest of those to check, so it should be the one that never
goes stale. Collection runs in a subprocess rather than reading
`request.session.items`, because that list is whatever the current invocation
selected -- under `-k`, `-x`, or a single-file run it is a fraction of the
suite, and a check that passes because it counted eight tests is worse than
no check.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"

# Both places the README commits to a number.
CLAIMS = (
    re.compile(r"\|\s*\*\*Tests\*\*\s*\|\s*([\d,]+),"),
    re.compile(r"what the ([\d,]+) tests actually assert"),
)


def _claimed_counts() -> list[int]:
    text = README.read_text(encoding="utf-8")
    found = []
    for pattern in CLAIMS:
        match = pattern.search(text)
        assert match, f"README no longer states its test count in the form {pattern.pattern!r}"
        found.append(int(match.group(1).replace(",", "")))
    return found


def _collected_count() -> int:
    """How many tests the suite actually has, collected without running them."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    # The last summary line is "N tests collected in Xs" (or "N/M tests collected").
    match = re.search(r"(\d+)\s*/?\s*(\d+)?\s*tests? collected", result.stdout)
    assert match, f"could not read a count out of pytest's collection output:\n{result.stdout[-2000:]}"
    return int(match.group(2) or match.group(1))


def test_readme_states_the_same_count_in_both_places():
    """The two claims are copies of each other, so they can disagree."""
    top, section_17 = _claimed_counts()
    assert top == section_17, (
        f"the summary table says {top} tests and section 17 says {section_17}"
    )


def test_readme_test_count_matches_the_suite():
    """The advertised number against the collected one.

    Off-by-one here is not a rounding error, it is a test that was added or
    deleted without the README noticing.
    """
    claimed = _claimed_counts()[0]
    actual = _collected_count()
    assert claimed == actual, (
        f"README advertises {claimed} tests; the suite collects {actual}. "
        f"Update both places in README.md (the summary table and the section 17 fold)."
    )
