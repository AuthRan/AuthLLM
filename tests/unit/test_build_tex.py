"""The arXiv LaTeX source, checked without a LaTeX installation.

`paper/build_tex.py` renders `paper/paper.md` into `paper/arxiv/main.tex`. The
machine that wrote it has no TeX, so none of its output has ever been compiled,
and "it looked right" is the only check it would otherwise have had. These are
the failures a compiler would catch, done by inspection instead:

  * unbalanced braces and environments, which are a hard error
  * a table row whose cell count disagrees with its column spec, which is a
    hard error and the easiest thing for a converter to get wrong
  * an unescaped `_` or `#`, which is a hard error outside math
  * markdown that survived the conversion, which is not an error and is worse:
    it compiles, and prints `**bold**` in the paper

And one thing a compiler could not catch. The markdown writes its own section
numbers into the heading text and the prose cites them ("section 4.7.1"), while
LaTeX generates its own. If those two disagree the paper compiles perfectly and
every cross-reference in it is wrong, so the numbering is simulated here and
compared against what the markdown claims.

This is not a substitute for compiling it once before submission.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PAPER = REPO / "paper" / "paper.md"
TEX = REPO / "paper" / "arxiv" / "main.tex"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """A fresh build, so the test cannot pass on a stale committed file.

    Built into a temporary directory rather than over `paper/arxiv/`, which is
    committed: a test that rewrote it would dirty the working tree every time
    the paper had moved ahead of its last build.
    """
    outdir = tmp_path_factory.mktemp("arxiv")
    subprocess.run([sys.executable, str(REPO / "paper" / "build_tex.py"),
                    "--outdir", str(outdir)],
                   cwd=REPO, check=True, capture_output=True)
    return outdir / "main.tex"


@pytest.fixture(scope="module")
def tex(built: Path) -> str:
    return built.read_text()


@pytest.fixture(scope="module")
def body(tex: str) -> str:
    """The source minus verbatim blocks and comments, where LaTeX rules apply."""
    stripped = re.sub(r"\\begin\{verbatim\}.*?\\end\{verbatim\}", "", tex, flags=re.S)
    return re.sub(r"(?<!\\)%.*", "", stripped)


def test_braces_balance(body: str) -> None:
    without_escaped = re.sub(r"\\[{}]", "", body)
    assert without_escaped.count("{") == without_escaped.count("}")


def test_environments_open_and_close_in_order(body: str) -> None:
    stack: list[str] = []
    for match in re.finditer(r"\\(begin|end)\{(\w+\*?)\}", body):
        if match.group(1) == "begin":
            stack.append(match.group(2))
        else:
            assert stack, f"\\end{{{match.group(2)}}} with nothing open"
            assert stack.pop() == match.group(2), f"\\end{{{match.group(2)}}} out of order"
    assert not stack, f"never closed: {stack}"


def test_no_unescaped_specials(body: str) -> None:
    """`_` and `#` outside math are a compile error, and this paper has no math.

    `$`, `&` and `%` are excluded because they have legitimate unescaped uses
    here -- `$\\pm$`, tabular separators, and comments -- and are covered by the
    brace and column-count checks instead.
    """
    for char in ("_", "#"):
        found = re.findall(rf"(?<!\\)\{char}", body)
        assert not found, f"{len(found)} unescaped {char!r}"


def test_no_markdown_survived(body: str) -> None:
    for name, pattern in [("bold", r"\*\*"), ("code span", "`"),
                          ("table row", r"^\|"), ("image", r"!\[")]:
        assert not re.search(pattern, body, re.M), f"markdown {name} reached the LaTeX"
    # The converter parks spans in private-use code points while it escapes
    # around them. One reaching the output means an expansion did not close.
    assert not re.search(r"[\ue000-\ue002]", body), "unexpanded placeholder"


def test_every_table_row_matches_its_column_spec(body: str) -> None:
    for match in re.finditer(r"\\begin\{tabular\}\{([^}]*)\}(.*?)\\end\{tabular\}", body, re.S):
        columns = len(match.group(1))
        for row in match.group(2).split(r"\\"):
            row = row.strip()
            if not row or "&" not in row:
                continue  # a rule, not a row
            cells = len(re.split(r"(?<!\\)&", row))
            assert cells == columns, f"{cells} cells against {columns} columns: {row[:60]!r}"


def test_figures_are_copied_beside_the_source(tex: str, built: Path) -> None:
    references = re.findall(r"\\includegraphics\[[^\]]*\]\{([^}]*)\}", tex)
    assert references, "the paper has figures; the LaTeX has none"
    for reference in references:
        assert (built.parent / reference).exists(), f"{reference} not copied"


def test_the_committed_package_is_current(tex: str) -> None:
    """`paper/arxiv/main.tex` is committed, so it has to match the paper."""
    assert TEX.exists(), "run python paper/build_tex.py"
    assert TEX.read_text() == tex, (
        "paper/arxiv/main.tex is stale; run python paper/build_tex.py")


def test_status_block_is_not_submitted(tex: str) -> None:
    """The draft's working state -- what is left, which venue -- is not a paper."""
    assert "Status: draft" not in tex
    assert "venue decision" not in tex


def markdown_numbers() -> list[tuple[str, str]]:
    numbers = []
    in_fence = False
    for line in PAPER.read_text().split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^#{2,4} ([A-G0-9]+(?:\.\d+)*)\.?\s+(.*)$", line)
        if match:
            numbers.append((match.group(1), match.group(2)))
    return numbers


def latex_numbers(tex: str) -> list[tuple[str, str]]:
    """Simulate LaTeX's section counters, including the switch to letters."""
    body = re.sub(r"\\begin\{verbatim\}.*?\\end\{verbatim\}", "", tex, flags=re.S)
    section = subsection = subsubsection = 0
    appendix = False
    numbers = []
    # `\appendix` takes no argument, so it is matched separately from the
    # sectioning commands rather than by the same group.
    for match in re.finditer(r"\\appendix\b|\\(section|subsection|subsubsection)(\*?)\{(.*?)\}", tex):
        if match.group(0).startswith(r"\appendix"):
            appendix, section = True, 0
            continue
        kind, starred, title = match.group(1), match.group(2), match.group(3)
        if starred:
            continue  # Abstract, References, the Appendix divider
        if kind == "section":
            section += 1
            subsection = subsubsection = 0
            label = chr(64 + section) if appendix else str(section)
        elif kind == "subsection":
            subsection += 1
            subsubsection = 0
            label = f"{chr(64 + section) if appendix else section}.{subsection}"
        else:
            subsubsection += 1
            label = f"{chr(64 + section) if appendix else section}.{subsection}.{subsubsection}"
        numbers.append((label, title))
    assert body  # the fixture's stripping is exercised above
    return numbers


def test_latex_reproduces_the_numbers_the_prose_cites(tex: str) -> None:
    """Section 4.7.1 has to come out 4.7.1, and Appendix C has to come out C.

    The paper refers to its own sections by number in prose, so LaTeX numbering
    that disagrees with the markdown's would be wrong everywhere at once and
    would still compile.
    """
    claimed = markdown_numbers()
    produced = latex_numbers(tex)
    assert len(claimed) == len(produced), (
        f"{len(claimed)} numbered headings in the markdown, {len(produced)} in the LaTeX")
    for (claimed_number, title), (produced_number, _) in zip(claimed, produced):
        assert claimed_number == produced_number, (
            f"'{title}' is {claimed_number} in the paper and {produced_number} in the LaTeX")
