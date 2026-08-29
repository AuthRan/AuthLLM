"""Render paper/paper.md into arXiv-ready LaTeX.

The same contract `build_page.py` has for the web version: the markdown is the
one source of truth, the numbers in it are regenerated from the ledgers by
`scripts/update_paper_counts.py`, and this only changes presentation. Nothing
here is allowed to restate a number.

    python paper/build_tex.py

Writes paper/arxiv/main.tex and copies the figures it references into
paper/arxiv/figures/, which is the whole submission: arXiv wants the sources,
and `tar czf arxiv.tar.gz -C paper/arxiv .` is the upload.

    NOTE: this machine has no TeX installation, so the output of this script has
    never been compiled. It is written to be conservative rather than clever --
    no package outside a base texlive, no float placement games, no macros
    beyond one -- but the first `pdflatex` run is still an unverified step. See
    paper/arxiv/README.md.

Section numbering is LaTeX's, not the markdown's. The markdown writes its own
numbers into the heading text (`## 4. Results`, `### 4.7.1 ...`) and the prose
refers to them as "section 4.7.1", so the numbers have to survive exactly. They
do because the depth is taken from the number itself -- three components means
\\subsubsection -- and because the appendices switch LaTeX to letters at the
same point the markdown does.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "paper" / "paper.md"
OUTDIR = ROOT / "paper" / "arxiv"
OUT = OUTDIR / "main.tex"
FIGDIR = OUTDIR / "figures"
# arXiv's submission form truncates the abstract field past roughly this many
# characters. Checked at build time so it is not discovered while submitting.
ABSTRACT_CAP = 1920

# The author block is the one thing in this file that is not derived from the
# markdown, because the markdown does not carry it. Edit here.
AUTHOR = "Ashutosh Ranjan"
AFFILIATION = "Independent Researcher"
CONTACT = "authran.off@gmail.com"

# Kept as one named string so `--anonymous` removes the whole block rather than
# matching a template fragment that a later edit could silently reword.
AUTHOR_BLOCK = (
    r"\author{@AUTHOR@ \\ \small @AFFILIATION@ \\" "\n"
    r"        \small \href{mailto:@CONTACT@}{\texttt{@CONTACT@}}}"
)

# Private-use code points, so a placeholder cannot collide with anything in the
# text and is not touched by the escaper.
CODE, BOLD, ITAL = "\ue000", "\ue001", "\ue002"

ESCAPES = [
    ("\\", "\\textbackslash{}"),  # first, or it would escape the escapes
    ("{", "\\{"), ("}", "\\}"), ("$", "\\$"), ("&", "\\&"),
    ("#", "\\#"), ("_", "\\_"), ("%", "\\%"),
    ("~", "\\textasciitilde{}"), ("^", "\\textasciicircum{}"),
    ("<", "\\textless{}"), (">", "\\textgreater{}"),
]

UNICODE = {
    "\u2014": "---", "\u2013": "--", "\u00a7": "\\S{}",
    "\u00b1": "$\\pm$", "\u221d": "$\\propto$", "\u00e1": "\\'a",
}


def escape(text: str) -> str:
    for char, replacement in ESCAPES:
        text = text.replace(char, replacement)
    for char, replacement in UNICODE.items():
        text = text.replace(char, replacement)
    return text


def inline(text: str) -> str:
    """Markdown inline spans to LaTeX, escaping everything that is not a span.

    Code spans are pulled out first so that a `%` or `_` inside one is escaped
    as text rather than read as markup, and bold before italic so that `**` is
    not seen as two `*`.
    """
    codes: list[str] = []
    bolds: list[str] = []
    itals: list[str] = []

    def take(store: list[str], marker: str):
        def repl(match: re.Match) -> str:
            store.append(match.group(1))
            return f"{marker}{len(store) - 1}{marker}"
        return repl

    text = re.sub(r"`([^`]+)`", take(codes, CODE), text)
    text = re.sub(r"\*\*(.+?)\*\*", take(bolds, BOLD), text, flags=re.S)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", take(itals, ITAL), text, flags=re.S)

    text = escape(text)

    def expand(text: str) -> str:
        def code(match: re.Match) -> str:
            body = escape(codes[int(match.group(1))])
            # Paths and run ids are long and unbreakable, and would run into the
            # margin. Let them break after a separator, which is where a reader
            # would break them anyway.
            body = body.replace("/", "/\\allowbreak{}")
            body = body.replace("\\_", "\\_\\allowbreak{}")
            return f"\\texttt{{{body}}}"

        text = re.sub(f"{CODE}(\\d+){CODE}", code, text)
        text = re.sub(f"{BOLD}(\\d+){BOLD}",
                      lambda m: f"\\textbf{{{expand(escape(bolds[int(m.group(1))]))}}}", text)
        text = re.sub(f"{ITAL}(\\d+){ITAL}",
                      lambda m: f"\\emph{{{expand(escape(itals[int(m.group(1))]))}}}", text)
        return text

    # Bold and italic bodies may hold code placeholders, so expansion repeats
    # until it reaches a fixed point rather than assuming one level of nesting.
    for _ in range(5):
        expanded = expand(text)
        if expanded == text:
            break
        text = expanded
    return text


def alignment(separator: str) -> str:
    spec = ""
    for cell in [c.strip() for c in separator.strip().strip("|").split("|")]:
        if cell.endswith(":") and cell.startswith(":"):
            spec += "c"
        elif cell.endswith(":"):
            spec += "r"
        else:
            spec += "l"
    return spec


def row_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def table(lines: list[str]) -> str:
    header, separator, *body = lines
    spec = alignment(separator)
    # Nine columns of numbers do not fit a 6.5in text block, and a three-column
    # table scaled to the same width would be magnified into something absurd.
    # Which is which cannot be known without typesetting it, so the decision is
    # left to LaTeX: measure the table, shrink it only if it overruns.
    out = [
        "",
        "\\begin{center}",
        "\\small",
        "\\setbox0=\\hbox{%",
        f"\\begin{{tabular}}{{{spec}}}",
        "\\toprule",
        " & ".join(inline(c) for c in row_cells(header)) + " \\\\",
        "\\midrule",
    ]
    for line in body:
        out.append(" & ".join(inline(c) for c in row_cells(line)) + " \\\\")
    out += [
        "\\bottomrule",
        "\\end{tabular}}",
        "\\ifdim\\wd0>\\linewidth\\resizebox{\\linewidth}{!}{\\usebox0}\\else\\usebox0\\fi",
        "\\end{center}",
        "",
    ]
    return "\n".join(out)


def heading(level: int, text: str, state: dict) -> str:
    """A markdown heading to its LaTeX sectioning command.

    The markdown carries its own section numbers and the prose cites them, so
    the depth is read off the number -- `4.7.1` is three deep -- and the number
    itself is dropped for LaTeX to regenerate.
    """
    if text == "Abstract":
        state["in_abstract"] = True
        return "\\begin{abstract}"

    if text == "References":
        return "\\section*{References}\n\\begingroup\n\\small\n\\setlength{\\parindent}{0pt}"

    if text == "Appendix":
        # The divider, then the switch to letters. Everything after this point
        # is an appendix, and `## A. ...` becomes LaTeX's A.
        state["appendix"] = True
        return "\\section*{Appendix}"

    match = re.match(r"^([A-G0-9]+(?:\.\d+)*)\.?\s+(.*)$", text)
    if match:
        number, title = match.group(1), match.group(2)
        depth = number.count(".") + 1
    else:
        number, title, depth = "", text, level - 1

    command = {1: "section", 2: "subsection", 3: "subsubsection"}.get(depth, "paragraph")
    prefix = ""
    if state.get("appendix") and not state.get("appendix_started") and command == "section":
        prefix = "\\appendix\n"
        state["appendix_started"] = True
    return f"{prefix}\\{command}{{{inline(title)}}}"


def figure(path: str, alt: str, caption: str | None) -> str:
    name = Path(path).name
    body = caption if caption else alt
    return "\n".join([
        "",
        "\\begin{figure}[htbp]",
        "\\centering",
        f"\\includegraphics[width=\\linewidth]{{figures/{name}}}",
        f"\\caption{{{inline(body)}}}",
        "\\end{figure}",
        "",
    ])


PREAMBLE = r"""\documentclass[11pt]{article}

% Deliberately a base-texlive preamble: arXiv builds it without extra packages,
% and this file has not been compiled on the machine that generated it.
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[margin=1in]{geometry}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{caption}
\usepackage{microtype}
\usepackage[hidelinks]{hyperref}

\captionsetup{font=small}
\setlength{\emergencystretch}{3em}
\sloppy

\title{@TITLE@}
@AUTHOR_BLOCK@
\date{}

\begin{document}
\maketitle
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    # The test suite builds into a temporary directory: regenerating over the
    # committed package on every test run would leave the working tree dirty
    # whenever the paper had moved ahead of its last build.
    parser.add_argument("--outdir", type=Path, default=OUTDIR,
                        help="Where to write main.tex and figures/")
    # NeurIPS 2026's main track is double-blind and forbids identifying
    # information; the Pre-to-Post call does not say whether it inherits that.
    # The prose carries nothing identifying -- no repository, no link to the
    # published version, no name -- so blinding is exactly this one title
    # block, and is a flag rather than a pass over the paper.
    parser.add_argument("--anonymous", action="store_true",
                        help="Omit the author block, for a double-blind venue")
    args = parser.parse_args()
    outdir = args.outdir
    out_path = outdir / "main.tex"
    figdir = outdir / "figures"

    raw = SOURCE.read_text()
    title = re.search(r"^# (.+)$", raw, re.M).group(1)

    # The status block is working state -- what is left to do, which venue --
    # and has no place in a submission. It sits between the title and the rule.
    body = raw.split("\n---\n", 1)[1]
    # The spans scripts/update_paper_counts.py rewrites are marked with HTML
    # comments. They render as nothing in markdown and are noise here.
    body = re.sub(r"<!--/?(?:runs|compute|exponents)-->", "", body)

    lines = body.split("\n")
    out: list[str] = []
    state: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            out += ["", "\\begin{verbatim}", *block, "\\end{verbatim}", ""]
            continue

        if line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(table(block))
            continue

        image = re.match(r"^!\[(.*?)\]\((.*?)\)\s*$", line)
        if image:
            alt, path = image.group(1), image.group(2)
            # The caption is the italic paragraph that follows the image.
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            caption = None
            if j < len(lines) and lines[j].lstrip().startswith("*"):
                block = []
                while j < len(lines) and lines[j].strip():
                    block.append(lines[j])
                    j += 1
                text = " ".join(block).strip()
                if text.startswith("*") and text.endswith("*"):
                    caption = text.strip("*")
                    i = j
            out.append(figure(path, alt, caption))
            if caption is None:
                i += 1
            continue

        head = re.match(r"^(#{2,4}) (.+)$", line)
        if head:
            if state.pop("in_abstract", False):
                out.append("\\end{abstract}")
            if state.pop("in_references", False):
                out.append("\\endgroup")
            rendered = heading(len(head.group(1)), head.group(2).strip(), state)
            if head.group(2).strip() == "References":
                state["in_references"] = True
            out += ["", rendered, ""]
            i += 1
            continue

        bullet = re.match(r"^(\s*)[-*] (.+)$", line)
        number = re.match(r"^(\s*)\d+\. (.+)$", line)
        if bullet or number:
            kind = "itemize" if bullet else "enumerate"
            items: list[str] = []
            pattern = r"^(\s*)[-*] (.+)$" if bullet else r"^(\s*)\d+\. (.+)$"
            while i < len(lines):
                match = re.match(pattern, lines[i])
                if match:
                    items.append(match.group(2))
                    i += 1
                elif lines[i].startswith("  ") and lines[i].strip() and items:
                    items[-1] += " " + lines[i].strip()  # continuation line
                    i += 1
                else:
                    break
            # A reference entry is a list item in the markdown but a bibliography
            # line in the paper, so it is set as a hanging paragraph instead.
            if state.get("in_references"):
                for item in items:
                    out += ["", f"\\hangindent=1.5em\\hangafter=1 {inline(item)}\\par", ""]
            else:
                out.append(f"\\begin{{{kind}}}")
                out += [f"\\item {inline(item)}" for item in items]
                out.append(f"\\end{{{kind}}}")
            continue

        if not line.strip():
            out.append("")
            i += 1
            continue

        # A prose paragraph: join its wrapped lines so LaTeX does its own
        # wrapping rather than inheriting the markdown's 80 columns.
        block = []
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(\||#{2,4} |```|!\[|\s*[-*] |\s*\d+\. )", lines[i]):
            block.append(lines[i].strip())
            i += 1
        out += ["", inline(" ".join(block)), ""]

    if state.pop("in_abstract", False):
        out.append("\\end{abstract}")
    if state.pop("in_references", False):
        out.append("\\endgroup")

    # \author{} rather than a dropped line: \maketitle without an author warns,
    # and an empty group is what the NeurIPS template's anonymous mode leaves.
    block = "\\author{}" if args.anonymous else AUTHOR_BLOCK
    preamble = (PREAMBLE
                .replace("@AUTHOR_BLOCK@", block)
                .replace("@TITLE@", escape(title))
                .replace("@AUTHOR@", escape(AUTHOR))
                .replace("@AFFILIATION@", escape(AFFILIATION))
                .replace("@CONTACT@", escape(CONTACT)))
    text = preamble + "\n".join(out) + "\n\n\\end{document}\n"
    # Collapse the runs of blank lines the block handling leaves behind.
    text = re.sub(r"\n{3,}", "\n\n", text)

    outdir.mkdir(parents=True, exist_ok=True)
    figdir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)

    # arXiv's submission form wants the abstract as plain text, separately from
    # the source. Taken from the same markdown so the two cannot disagree.
    abstract = body.split("## Abstract", 1)[1].split("\n## ", 1)[0]
    paragraphs = [" ".join(block.split())
                  for block in abstract.strip().split("\n\n") if block.strip()]
    plain = "\n\n".join(paragraphs) + "\n"
    (outdir / "abstract.txt").write_text(plain)
    abstract_chars = len(plain)

    copied = 0
    for match in re.finditer(r"^!\[.*?\]\((.*?)\)\s*$", body, re.M):
        source = (SOURCE.parent / match.group(1)).resolve()
        shutil.copy2(source, figdir / source.name)
        copied += 1

    print(f"{len(text.splitlines())} lines, {copied} figures -> {out_path}")
    # Say so at build time rather than leaving it to be discovered in the
    # submission form.
    if abstract_chars > ABSTRACT_CAP:
        print(f"abstract.txt is {abstract_chars} characters, over arXiv's "
              f"~{ABSTRACT_CAP:,} cap -- cut it before pasting")
    else:
        print(f"abstract.txt is {abstract_chars} characters, inside arXiv's "
              f"~{ABSTRACT_CAP:,} cap")


if __name__ == "__main__":
    main()
