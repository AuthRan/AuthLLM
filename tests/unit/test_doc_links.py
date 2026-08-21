"""Every internal link in the documentation has to point at something.

This exists because the rot is real and silent. `configs/train/sft_chat.yaml`
shipped pointing at a README section 10.7 that did not exist and at sweep logs
that were never produced; README section 18 spent a commit claiming FSDP and
the browser frontend were unbuilt after both had shipped. Nothing catches that
-- a dead anchor renders as ordinary text and a missing file link is a 404 only
someone else ever sees.

The anchor rule implemented here is GitHub's: lowercase, drop everything that
is not a word character, whitespace or hyphen, then replace whitespace with
hyphens. `## 10.6 Sequence packing — the 89% that was padding` becomes
`#106-sequence-packing--the-89-that-was-padding`, the doubled hyphen coming
from the spaces that surrounded the em dash.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_DIRS = ("results", "learning", "space")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)
FENCE = re.compile(r"^\s*```", re.M)
# [text](target) where target is not an external URL or a bare mailto.
LINK = re.compile(r"\[[^\]]*\]\((?!https?:|mailto:)([^)\s]+)\)")


def _documents() -> list[Path]:
    docs = [REPO_ROOT / "README.md", REPO_ROOT / "SPEC.md"]
    for directory in DOC_DIRS:
        docs.extend(sorted((REPO_ROOT / directory).glob("*.md")))
    return [d for d in docs if d.exists()]


def _strip_code_fences(text: str) -> str:
    """Headings inside a fenced block are code, not headings -- and a `#`
    comment in a shell snippet is not a section anyone can link to."""
    out, inside = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def _slug(heading: str) -> str:
    text = re.sub(r"`", "", heading.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


def _anchors(path: Path) -> set[str]:
    return {_slug(m.group(1)) for m in HEADING.finditer(_strip_code_fences(path.read_text(encoding="utf-8")))}


DOCUMENTS = _documents()


@pytest.mark.parametrize("doc", DOCUMENTS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_internal_links_resolve(doc: Path) -> None:
    broken = []
    for match in LINK.finditer(doc.read_text(encoding="utf-8")):
        target = match.group(1)
        file_part, _, anchor = target.partition("#")

        if file_part:
            resolved = (doc.parent / file_part).resolve()
            if not resolved.exists():
                broken.append(f"{target} -- no such file")
                continue
        else:
            resolved = doc

        if anchor and resolved.suffix == ".md" and anchor not in _anchors(resolved):
            broken.append(f"{target} -- no such heading in {resolved.name}")

    assert not broken, f"{doc.relative_to(REPO_ROOT)} has dead links:\n  " + "\n  ".join(broken)


def test_the_checker_would_notice_a_dead_anchor(tmp_path: Path) -> None:
    """A test that never fails is not a test. This pins the slug rule against
    the awkward heading style this repo actually uses."""
    doc = tmp_path / "sample.md"
    doc.write_text("## 10.6 Sequence packing — the 89% that was padding\n", encoding="utf-8")

    assert _anchors(doc) == {"106-sequence-packing--the-89-that-was-padding"}
    assert "106-sequence-packing" not in _anchors(doc)


def test_headings_inside_code_fences_are_not_anchors(tmp_path: Path) -> None:
    doc = tmp_path / "sample.md"
    doc.write_text("## Real heading\n\n```\n# not a heading, a shell comment\n```\n", encoding="utf-8")
    assert _anchors(doc) == {"real-heading"}
