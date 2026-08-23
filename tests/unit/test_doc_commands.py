"""The commands in the documentation have to describe runs that could happen.

`test_doc_links.py` checks that every markdown *link* points at something.
Nothing checked the commands, and a command is the part a reader actually
copies. Two different failures live in there, and only one of them is a
missing file.

The first is an ordinary dead path: a snippet naming `scripts/generate.py` or
`configs/data/tinystories.yaml` when neither was ever built.

The second has no missing file at all and is the one that shipped. README's
preference-tuning block trained with `configs/train/dpo_hh.yaml`, whose
`max_steps` is 400, into `checkpoints/dpo_hh`, and then evaluated
`checkpoints/dpo_hh/step_1495.pt` -- a checkpoint that config cannot produce.
1,495 steps is the *full epoch*, the run this project measured and explicitly
declined to ship, and `results/preference-tuning.md` gave the same block as
`step_400.pt`. So the two documents disagreed about which checkpoint the
headline preference numbers came from, and the README named the rejected one.
No file was missing, no link was dead, and the number in the surrounding prose
was right.

A note on what cannot be checked here: `checkpoints/` and `data/` are
gitignored, so they do not exist in the clean checkout CI runs from, and
asserting a checkpoint file exists would fail for everyone who has not trained
one. That is exactly why the second check compares a checkpoint against the
*config* that would produce it -- the config is tracked, so the claim stays
verifiable from a bare clone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories git actually tracks. checkpoints/ and data/ are deliberately
# absent -- see the module docstring.
TRACKED_DIRS = ("ashugpt", "scripts", "configs", "tests", "results", "logs", "resources", "learning", "space")

TRACKED_PATH = re.compile(r"(?<![\w/.-])((?:" + "|".join(TRACKED_DIRS) + r")/[\w./-]+)")
TRAIN_CONFIG = re.compile(r"--train\s+(configs/train/[\w./-]+\.yaml)")
CHECKPOINT_DIR = re.compile(r"--checkpoint-dir\s+(checkpoints/[\w./-]+)")
CHECKPOINT_FILE = re.compile(r"(checkpoints/[\w./-]+)/step_(\d+)\.pt")

# SPEC.md is excluded on purpose. It says of itself that it is "the original
# design spec plus a living milestone-by-milestone log", and its Commands and
# Project Structure blocks are the *original* half -- a plan from before the
# code existed, whose deviations are already recorded row by row in the
# milestone table. Asserting that a 2026-08-16 plan matches the tree would be
# asserting the project never learned anything.
DOCS = ["README.md"]


def _documents() -> list[Path]:
    docs = [REPO_ROOT / name for name in DOCS]
    for directory in ("results", "learning"):
        docs.extend(sorted((REPO_ROOT / directory).glob("*.md")))
    return [d for d in docs if d.exists()]


def _code_blocks(path: Path) -> list[str]:
    """Every fenced block in a document, fences excluded."""
    blocks, current, inside = [], [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```"):
            if inside and current:
                blocks.append("\n".join(current))
            current, inside = [], not inside
            continue
        if inside:
            current.append(line)
    if inside and current:
        blocks.append("\n".join(current))
    return blocks


def _is_placeholder(path: str) -> bool:
    """`<corpus.txt>`, `configs/model/*.yaml` and the like are illustrations."""
    return any(ch in path for ch in "*$<>{}")


# --- dead paths --------------------------------------------------------------


def test_every_tracked_path_in_a_command_exists():
    """A snippet naming a tracked file has to name one that is there.

    Restricted to directories git tracks, so this passes from a bare clone.
    Paths under checkpoints/ and data/ are an artifact of having trained
    something, and are checked by the next test instead.
    """
    missing: list[str] = []
    for doc in _documents():
        for block in _code_blocks(doc):
            for match in TRACKED_PATH.finditer(block):
                path = match.group(1).rstrip(".,;:)")
                if _is_placeholder(path):
                    continue
                if not (REPO_ROOT / path).exists():
                    missing.append(f"{doc.relative_to(REPO_ROOT)}: {path}")
    assert not missing, "commands name files that are not in the tree:\n  " + "\n  ".join(missing)


# --- checkpoints that the cited config could not have written ----------------


def _producible_steps(config_path: Path) -> tuple[int | None, int | None]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return config.get("max_steps"), config.get("checkpoint_interval")


def test_cited_checkpoints_match_the_config_that_would_produce_them():
    """step_N.pt has to be a step the config in the same block reaches.

    Only checkpoints written into that block's own `--checkpoint-dir` are
    checked. A block routinely names checkpoints from earlier stages too --
    `--init-from checkpoints/sft_dolly_packed3e5/step_940.pt` -- and the
    config that produced those is not the one being run here.

    The trainer writes at every checkpoint_interval and again at max_steps
    (trainer.py special-cases the last step, after the Alpaca run threw away
    the weights that produced its final numbers), so those are the steps a
    reader can actually point at.
    """
    wrong: list[str] = []
    for doc in _documents():
        for block in _code_blocks(doc):
            configs = TRAIN_CONFIG.findall(block)
            out_dirs = set(CHECKPOINT_DIR.findall(block))
            if not configs or not out_dirs:
                continue
            config_path = REPO_ROOT / configs[0]
            if not config_path.exists():  # the previous test reports this
                continue
            max_steps, interval = _producible_steps(config_path)
            if max_steps is None:
                continue
            for directory, step_text in CHECKPOINT_FILE.findall(block):
                if directory not in out_dirs:
                    continue
                step = int(step_text)
                producible = step == max_steps or (interval and step <= max_steps and step % interval == 0)
                if not producible:
                    wrong.append(
                        f"{doc.relative_to(REPO_ROOT)}: {directory}/step_{step}.pt is not a step "
                        f"{configs[0]} reaches (max_steps={max_steps}, checkpoint_interval={interval})"
                    )
    assert not wrong, "commands evaluate checkpoints their own config cannot produce:\n  " + "\n  ".join(wrong)


def test_the_two_dpo_documents_cite_the_same_checkpoint():
    """The regression, stated directly rather than inferred.

    README section 15 and results/preference-tuning.md give the same
    reproduction for the same stage. When they disagreed, the README pointed
    at the epoch run -- 3.7x the compute, and the run this project measured
    and chose not to ship.
    """
    cited = set()
    for name in ("README.md", "results/preference-tuning.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        cited.update(re.findall(r"dpo=checkpoints/dpo_hh/step_(\d+)\.pt", text))
    assert cited == {"400"}, f"the shipped DPO run is 400 steps, but the docs cite {sorted(cited)}"


# --- the scan itself ---------------------------------------------------------


def test_the_scan_actually_reads_commands():
    """If the fence parser returned nothing, both checks above would pass."""
    readme_blocks = _code_blocks(REPO_ROOT / "README.md")
    assert len(readme_blocks) > 10, "README is mostly commands; this found almost none"
    joined = "\n".join(readme_blocks)
    assert "scripts/preference_tune.py" in joined
    assert TRACKED_PATH.search(joined), "no tracked path matched anywhere in the README's blocks"


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("python scripts/train.py --train configs/train/tiny_cpu.yaml", ["scripts/train.py", "configs/train/tiny_cpu.yaml"]),
        ("cat logs/dpo_hh.csv", ["logs/dpo_hh.csv"]),
        ("--checkpoint checkpoints/demo/step_150.pt", []),  # untracked, deliberately not matched
    ],
)
def test_path_pattern_matches_what_it_should(block, expected):
    assert [m.group(1) for m in TRACKED_PATH.finditer(block)] == expected
