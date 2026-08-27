"""Are the paper's conclusions properties of the data, or of how we normalized it?

The paper summarises every comparison as an *exponent*,
`alpha = log(shift) / log(p)`, where `shift` is how far the optimum moves and `p`
is the packing factor in supervised tokens per step. That is a choice of
normalization and not a measured law. It is the natural one -- it is the
quantity a practitioner would raise `p` to -- but nothing in the data says the
relationship is a power law in `p`, and if it is not, then comparing exponents
between two settings with *different* `p` compares partly the normalization.

That matters unevenly across the paper, and the split is worth making explicit:

  * The scale series (4.6) and the model-size series (4.7) hold `p` fixed to
    within 1% by construction -- 4.47x, 4.51x, 4.53x. Any monotone rescaling of
    `shift` preserves their ordering and very nearly preserves their margins, so
    those conclusions cannot be artefacts of this choice.
  * The length terciles (4.5) span `p` from 2.73x to 7.84x, and the two
    cross-corpus pairs (4.6) span 1.4x to 1.5x. Those comparisons do depend on
    the normalization, and this script measures how much.

So: recompute every conclusion under three normalizations and see which survive.

    power    alpha = log(shift)/log(p)        what the paper reports
    linear   c     = (shift-1)/(p-1)          shift linear in p instead
    sqrtrel  r     = shift/sqrt(p)            residual against the sqrt rule

Seed bounds are propagated *exactly* rather than by a delta approximation. The
ledger's bound is a symmetric half-width in log-exponent space, so it is first
converted back to a multiplicative factor `B` on the shift itself; each
statistic is then evaluated at `shift/B` and `shift*B` and the half-width of
that interval is taken. The intervals are asymmetric under the two non-log
normalizations, which is precisely why the delta method is not used.

    python scripts/check_normalization_robustness.py

Prints a table per conclusion. A conclusion that changes verdict between
normalizations is one the paper must not state without saying so.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPONENTS = REPO / "results" / "exponents.csv"

FORMS = ("power", "linear", "sqrtrel")


def load() -> dict[tuple[str, str], dict]:
    with EXPONENTS.open() as handle:
        return {(r["model"], r["dataset"]): {
            "p": float(r["packing_factor"]),
            "shift": float(r["shift"]),
            "bound": float(r["bound"]),
        } for r in csv.DictReader(handle)}


def statistic(row: dict, form: str) -> tuple[float, float]:
    """(value, symmetric half-width) for one cell under one normalization."""
    p, s = row["p"], row["shift"]
    # The ledger's bound is hypot(log spread_packed, log spread_unpacked)/log(p).
    # Undo the division to recover the log-space bound on the shift, then take
    # the multiplicative factor it corresponds to.
    log_bound = row["bound"] * math.log(p)
    lo, hi = s / math.exp(log_bound), s * math.exp(log_bound)

    if form == "power":
        f = lambda x: math.log(x) / math.log(p)
    elif form == "linear":
        f = lambda x: (x - 1.0) / (p - 1.0)
    elif form == "sqrtrel":
        f = lambda x: x / math.sqrt(p)
    else:
        raise ValueError(form)
    return f(s), (f(hi) - f(lo)) / 2.0


def compare(data, a, b, form) -> tuple[float, float]:
    v1, e1 = statistic(data[a], form)
    v2, e2 = statistic(data[b], form)
    return abs(v1 - v2), math.hypot(e1, e2)


def line(label, ratio, verdict_if_clears, verdict_if_not) -> str:
    verdict = verdict_if_clears if ratio >= 1.0 else verdict_if_not
    return f"    {label:<9} {ratio:5.2f}x its own noise   {verdict}"


def main() -> None:
    if not EXPONENTS.exists():
        raise SystemExit("run scripts/export_exponents.py first")
    d = load()

    def have(*keys):
        return all(k in d for k in keys)

    print(__doc__.split("\n\n")[0])
    print()

    print("How far does p move inside each comparison?")
    for name, keys in (
        ("scale series, 124M", [("124M", "alpaca_ninth"), ("124M", "alpaca_third"), ("124M", "alpaca")]),
        ("model size, whole corpus", [("124M", "alpaca"), ("30M", "alpaca"), ("7M", "alpaca")]),
        ("length terciles, 124M", [("124M", "alpaca_short"), ("124M", "alpaca_mid"), ("124M", "alpaca_long")]),
        ("cross-corpus pairs", [("124M", "alpaca_third"), ("124M", "dolly")]),
    ):
        keys = [k for k in keys if k in d]
        if not keys:
            continue
        ps = [d[k]["p"] for k in keys]
        note = "form-free" if max(ps) / min(ps) < 1.05 else "FORM-DEPENDENT"
        print(f"  {name:<26} p {min(ps):.2f}x to {max(ps):.2f}x  ({max(ps)/min(ps):.2f}x)  {note}")

    print("\n1. Scale moves the exponent (Alpaca whole against its random third).")
    print("   Expect this to survive everywhere: p is held to 1%.")
    if have(("124M", "alpaca"), ("124M", "alpaca_third")):
        for f in FORMS:
            g, b = compare(d, ("124M", "alpaca"), ("124M", "alpaca_third"), f)
            print(line(f, g / b, "clears", "DOES NOT CLEAR"))

    print("\n2. The packing-ratio trend across length terciles is unsupported.")
    print("   p spans 2.87x here, so this is the one most exposed to the choice.")
    if have(("124M", "alpaca_short"), ("124M", "alpaca_long"), ("124M", "alpaca_mid")):
        for f in FORMS:
            g, b = compare(d, ("124M", "alpaca_short"), ("124M", "alpaca_long"), f)
            vals = [statistic(d[("124M", c)], f)[0]
                    for c in ("alpaca_short", "alpaca_mid", "alpaca_long")]
            mono = (vals[0] < vals[1] < vals[2]) or (vals[0] > vals[1] > vals[2])
            print(line(f, g / b, "TREND SUPPORTED", "unsupported")
                  + f"   ({'monotone' if mono else 'not even monotone'})")

    print("\n3. Two corpora matched on the scale of the run agree.")
    for label, a, b in (
        ("Alpaca third vs Dolly whole", ("124M", "alpaca_third"), ("124M", "dolly")),
        ("Alpaca ninth vs Dolly third", ("124M", "alpaca_ninth"), ("124M", "dolly_third")),
    ):
        if not have(a, b):
            continue
        print(f"  {label}")
        for f in FORMS:
            g, bd = compare(d, a, b, f)
            print(line(f, g / bd, "DISAGREE", "agree within noise"))

    print("\nRead the verdict column, not the ratios: a conclusion is only as good as")
    print("its weakest normalization, and one that flips has to be reported as a")
    print("property of the summary rather than of the models.")


if __name__ == "__main__":
    main()
