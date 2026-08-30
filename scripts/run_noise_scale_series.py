"""Run the noise-scale measurement across the settings the paper argues about.

`scripts/measure_noise_scale.py` measures one setting and prints it. This runs
the set that `results/registered-prediction-noise-scale.md` names, and writes
`results/noise-scale.md` so the prediction can be scored off a table rather than
off scrollback.

The set is chosen by that registration, not here:

  * `alpaca`, `alpaca_third`, `alpaca_ninth` at 124M -- a corpus and two random
    subsets of it, whose exponents are 1.055, 0.670 and 0.385. They share an
    example distribution exactly, so agreement between them is the validity
    check and disagreement voids the rest.
  * the same corpus at 30M and 7M, where the model changes and the noise scale
    genuinely can differ. Exploratory, and marked so.

    python scripts/run_noise_scale_series.py            # the whole set
    python scripts/run_noise_scale_series.py --repeats 8   # a faster pass
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = REPO / ".venv" / "bin" / "python"
OUT = REPO / "results" / "noise-scale.md"

# (label, model config, checkpoint, corpus, of_record). The exponent and the
# supervised-token counts are NOT here: they are read from
# results/exponents.csv and from analyze_lr_scaling.SUPERVISED_TOKENS at run
# time. A first draft of this file typed them by hand and had alpaca_ninth at
# 1,875/8,494 against the real 1,824/8,263, which is the drift this repository
# has a test suite about.
SETTINGS = [
    ("124M, Alpaca whole", "medium.yaml", "medium/step_20000.pt", "alpaca", True),
    ("124M, Alpaca third", "medium.yaml", "medium/step_20000.pt", "alpaca_third", True),
    ("124M, Alpaca ninth", "medium.yaml", "medium/step_20000.pt", "alpaca_ninth", True),
    ("30M, Alpaca whole", "small.yaml", "small/step_18000.pt", "alpaca", False),
    ("7M, Alpaca whole", "mini.yaml", "mini/step_4400.pt", "alpaca", False),
]

MODEL_KEY = {"medium.yaml": "124M", "small.yaml": "30M", "mini.yaml": "7M"}


def exponent_of(model_config: str, corpus: str) -> float:
    """The measured exponent for one row of results/exponents.csv."""
    import csv

    path = REPO / "results" / "exponents.csv"
    if not path.exists():
        raise SystemExit(f"{path} missing; run scripts/export_exponents.py first")
    want = MODEL_KEY[model_config]
    for row in csv.DictReader(path.open()):
        if row["model"] == want and row["dataset"] == corpus:
            return float(row["exponent"])
    raise SystemExit(f"no {want}/{corpus} row in {path}")


# The per-size rows the script prints before its fit. `mean tokens` must rise
# with `rows` in expectation, and the registration's addendum commits to
# reporting it: a two-repeat smoke test produced 97.0, 47.5, 268.5 across sizes
# 1, 2 and 4, which is sampling noise wearing the shape of a measurement, and an
# R^2 of 0.9999 did nothing to reveal it.
ROW = re.compile(r"^\s*(\d+)\s+([\d.]+)\s+([-\d.e+]+)\s+(\d+)\s*$", re.M)

FIELDS = {
    "intercept": re.compile(r"\|G\|\^2\s+\(intercept\) = ([-\d.e+]+)"),
    "slope": re.compile(r"tr\(S\)\s+\(slope\)\s+= ([-\d.e+]+)"),
    "r2": re.compile(r"R\^2 of the fit\s+= ([-\d.]+)"),
    "b_simple": re.compile(r"B_simple = tr\(S\)/\|G\|\^2 = ([\d,]+) supervised tokens"),
}


def measure(config: str, checkpoint: str, corpus: str, repeats: int,
            packed: bool, sizes: list[int]) -> dict:
    command = [
        str(PYTHON), str(REPO / "scripts" / "measure_noise_scale.py"),
        "--model", str(REPO / "configs" / "model" / config),
        "--init-from", str(REPO / "checkpoints" / checkpoint),
        "--data", str(REPO / "data" / "sft" / f"{corpus}.jsonl"),
        "--repeats", str(repeats),
        "--sizes", *[str(n) for n in sizes],
    ]
    if packed:
        command.append("--packed")
    print(f"  measuring {corpus} on {config} ({'packed' if packed else 'padded'})",
          flush=True)
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-2000:], file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"measure_noise_scale.py failed for {corpus}/{config}")

    out = {}
    for name, pattern in FIELDS.items():
        match = pattern.search(result.stdout)
        out[name] = match.group(1).replace(",", "") if match else None
    tokens = [float(m.group(2)) for m in ROW.finditer(result.stdout)]
    out["mean_tokens"] = tokens
    out["monotonic"] = all(a < b for a, b in zip(tokens, tokens[1:])) if tokens else False
    out["raw"] = result.stdout
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=24)
    # The default in measure_noise_scale.py runs to 32 sequences, which is a
    # 32x512 backward pass and does not fit on an 11.26 GB card beside the
    # desktop and the inference server: the 124M model OOMs there. These are
    # measurement points for a fit that extrapolates, so a shorter lever arm
    # costs precision and not validity.
    parser.add_argument("--sizes", nargs="+", type=int, default=[1, 2, 4, 6, 8])
    parser.add_argument("--packed", action="store_true",
                        help="Measure the packed dataset as well as the padded one")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO / "scripts"))
    from analyze_lr_scaling import SUPERVISED_TOKENS  # noqa: E402

    rows = []
    for label, config, checkpoint, corpus, record in SETTINGS:
        tokens = SUPERVISED_TOKENS[corpus]
        got = measure(config, checkpoint, corpus, args.repeats, packed=False,
                      sizes=args.sizes)
        rows.append({"label": label, "corpus": corpus,
                     "exponent": exponent_of(config, corpus),
                     "pad": tokens[False], "pack": tokens[True],
                     "record": record, **got})

    lines = [
        "# Gradient noise scale, measured",
        "",
        "*Generated by `scripts/run_noise_scale_series.py`. Scored against*",
        "*`results/registered-prediction-noise-scale.md`, which was written before*",
        "*`scripts/measure_noise_scale.py` had ever been run.*",
        "",
        "`B_simple = tr(S)/|G|^2` in supervised tokens per step, so it compares",
        "directly against the padded and packed columns beside it.",
        "",
        "| setting | exponent | padded | packed | `B_simple` | packed / `B_simple` | R^2 | tokens rise with rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for r in rows:
        b = r["b_simple"]
        ratio = f"{r['pack'] / float(b):.2f}x" if b else "—"
        mono = "yes" if r["monotonic"] else "**no**"
        value = f"{int(float(b)):,}" if b else "fit failed"
        lines.append(
            f"| {r['label']} | {r['exponent']:.3f} | {r['pad']:,} | {r['pack']:,} | "
            f"{value} | {ratio} | {float(r['r2']):.4f} | {mono} |")

    bad = [r["label"] for r in rows if not r["monotonic"]]
    if bad:
        lines += [
            "",
            "## These settings did not measure anything",
            "",
            "`mean tokens` must rise with the number of rows in a batch. Where it",
            "does not, the estimator saw sampling noise and its fit describes that",
            "noise, whatever the R^2 says. Affected: " + ", ".join(bad) + ".",
        ]

    on_record = [r for r in rows if r["record"] and r["b_simple"]]
    if len(on_record) == len(SETTINGS[:3]):
        values = [float(r["b_simple"]) for r in on_record]
        spread = max(values) / min(values)
        lines += [
            "",
            "## The validity check",
            "",
            f"The corpus and its two random subsets span **{spread:.2f}x** in `B_simple`.",
            "The registration voids everything below if that exceeds 1.5x, on the",
            "grounds that three samples of one distribution cannot have three noise",
            "scales.",
        ]

    OUT.write_text("\n".join(lines) + "\n")
    print(f"\nwrote {OUT}")
    for r in rows:
        print(f"  {r['label']:<22} B_simple {r['b_simple'] or 'fit failed':>10}  "
              f"R^2 {r['r2']}")


if __name__ == "__main__":
    main()
