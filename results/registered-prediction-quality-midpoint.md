# Registered prediction: the middle point of the quality series

**Written 2026-08-29, with the ledger holding 1 row.** The sweep was launched
a few minutes earlier and `results/lr_scaling_quality2500.csv` had already
recorded its first run by the time this was saved. The full contents at
registration time, quoted so the claim is auditable rather than asserted:

```
    alpaca,packed_350,True,350,9e-05,1337,2.4193,2.4193,350,267,logs/lr_scaling_quality2500/alpaca_packed_350_lr9e-05_seed1337.csv
```

That row is `packed_350` at 9e-5, the **lowest rate in the packed arm**. A
single point at the edge of a grid cannot locate a minimum — the estimator in
`scripts/analyze_lr_scaling.py` fits a parabola and needs an interior point with
a rise on both sides — so nothing about the optimum, the shift or the exponent
was visible when the prediction below was fixed. It is still not a prediction
written against an empty ledger, and this file says so rather than rounding the
claim up.

**Drafted by the assistant and not reviewed by the author before registration.**
Recorded now because a prediction written after the runs land is worth nothing.
Strike it if you disagree; that costs nothing.

This is the sixth registered prediction in this project, and the second on the
size-versus-quality confound. It was already written down as a secondary,
explicitly-not-of-record item in
`results/registered-prediction-size-vs-quality.md`; this file promotes it to a
scored prediction and states its criterion before the data exists.

## What is being run

`checkpoints/medium/step_2500.pt` — 124M parameters at validation perplexity
39.4, against the 30M model's 38.0 — fine-tuned on the whole-Alpaca factorial.
Same architecture, same 512-token window, same 4.47x packing factor, same two
cells (`unpacked_1600`, `packed_350`), same estimator, same three seeds as every
other row in Appendix F.

This is the middle point of the quality series that §4.7.2 and §5 both record as
missing. It makes the quality axis three points at a fixed parameter count:

| base model | perplexity | exponent |
| --- | ---: | ---: |
| 124M, step 20,000 | 23.5 | 1.055 ± 0.128 |
| 124M, step 2,500 | 39.4 | **this run** |
| 124M, step 500 | 107.0 | 0.906 ± 0.204 |

## State this test is inherited into

H2 — that §4.7's trend is base-model quality rather than parameter count — is
already falsified at the extreme point. §4.7.2 took a 124M model to the 7M
model's perplexity, a 4.6x move, and the exponent stayed with the parameter
count. This run cannot repeat that test; it is a **replication at a second,
milder quality offset**, and it is registered because a control with one point
and a control with two points are different objects.

## Why the obvious margin does not work, stated in advance

The tempting criterion is the one §4.7.2 used: land near 124M's 1.055 and more
than a combined seed bound away from the model matched on quality, here 30M's
1.300. That criterion is unsatisfiable and it would be dishonest to register it.
The 124M-against-30M gap is 0.245 and the combined bound on it is 0.287, so the
two values §4.7 already reports do not separate from each other. A margin test
against 1.300 would fail even if this run reproduced 1.055 exactly.

This is the same weakness the earlier registration flagged when it declined to
put this point on record. The criterion below is chosen to be answerable.

## The prediction of record

**H1 — the exponent tracks parameter count.** Two conditions, both required:

1. **Side.** The measured exponent is closer to 124M's 1.055 than to 30M's
   1.300 — that is, it lands **below 1.178**, the midpoint of the two.
2. **Flatness.** The three-point 124M quality series stays flat: its spread
   (max − min across the three exponents) is no larger than **0.332**, the
   combined seed bound of the two points already measured (0.128 + 0.204).

**Point estimate.** 1.00. The two existing 124M points, interpolated on
log-perplexity — `log(39.4/23.5) / log(107.0/23.5)` = 0.34 of the way from 1.055
to 0.906 — give 1.004. I expect it between 0.90 and 1.10.

**What would falsify it.** An exponent at or above 1.178, that is, sitting with
the model matched on quality rather than with the one matched on parameter
count. That would not resurrect H2 — the step_500 point rules it out — but it
would mean the quality axis is **non-monotone**, which no reading currently on
the table predicts, and §4.7.2 would have to be rewritten from a clean control
into an unexplained one. Spread above 0.332 with the side condition still met is
the same warning in weaker form and should be reported, not smoothed over.

**A null-of-power outcome is possible and is not a pass.** If the run brackets
badly, loses seeds, or returns a bound so wide that both conditions are met
trivially, that is not evidence for H1. The bound is reported either way.

## Secondary, explicitly not of record

*The optima themselves.* §4.7.1 and §4.7.2 both found that a less-pretrained
base model wants a larger rate in both arms while leaving the ratio alone —
about 1.45x for a halved budget, and 4.0x/3.2x at step_500. step_2500 sits
between, so I expect the padded optimum near 4–6e-5 (against 2.81e-5 at
step_20000 and 1.12e-4 at step_500) and the packed optimum near 2–2.5e-4
(against 1.37e-4 and 4.36e-4). This is a guess about levels, not about the
exponent, and discriminates nothing.

It is however what the grid was chosen on, and it is recorded here so that a
grid that fails to bracket is visible as a bad guess rather than rewritten
afterwards: `unpacked_1600` at 2e-5, 3e-5, 6e-5, 9e-5, 1.5e-4 and `packed_350`
at 9e-5, 1.5e-4, 2.5e-4, 4e-4, 6e-4, seed 1337, with the bracketing window
replicated to seeds 1338 and 1339. If a minimum lands on an edge the window is
extended, under the rule Appendix C already applies to every other ledger.

## How it will be scored

`python scripts/export_exponents.py` after registering the ledger in it and in
`scripts/update_paper_counts.py`, then the new row of `results/exponents.csv`
against the two conditions above. No discretion.

## Limitations recorded in advance

1. **The ledger was not empty**, as the header records. One edge point of ten
   had landed. It carries no information about the minimum, but the honest
   statement is "one row", not "none".
2. **An early checkpoint of a big model is not a converged small one**, at step
   2,500 of 20,000 no less than at step 500. Matching on perplexity matches one
   number and not the state of the weights. Inherited from the earlier
   registration and undiminished by this run.
3. **This point is milder than the one already measured**, so it can corroborate
   the step_500 result but cannot outweigh it. If the two disagree, the extreme
   point is the better-powered measurement and the disagreement itself is the
   finding.
4. **One corpus, one packing factor.** Alpaca whole at 4.47x, as everywhere else
   on this axis.

---

# Scoring, 2026-08-30

**Both conditions of record are met. H1 is confirmed, and the point estimate was
wrong in a way worth stating plainly.**

`results/lr_scaling_quality2500.csv` holds 22 runs. Every one of the six curves —
two cells at three seeds — brackets its optimum with no seed dropped and no
window extended, which is the cleanest ledger in this project and is not what the
last two additions managed.

| base model | perplexity | lr* padded | lr* packed | shift | exponent |
| --- | ---: | ---: | ---: | ---: | ---: |
| 124M, step 20,000 | 23.5 | 2.81e-5 | 1.37e-4 | 4.86x | 1.055 ± 0.128 |
| **124M, step 2,500** | **39.4** | **5.22e-5** | **2.85e-4** | **5.46x** | **1.133 ± 0.107** |
| 124M, step 500 | 107.0 | 1.12e-4 | 4.36e-4 | 3.89x | 0.906 ± 0.204 |

**Condition 1, side.** The criterion was to land below 1.178, the midpoint of
124M's 1.055 and 30M's 1.300. Measured **1.133**. Passes — **by 0.045**. That is
a narrow pass and much narrower than §4.7.2's step_500 result, which cleared its
registered margin by 2.7x. It should be read as such.

**Condition 2, flatness.** The criterion was a spread across the three 124M
points no larger than 0.332. Measured **0.227**. Passes.

**The point estimate was wrong.** I registered 1.00 and said I expected the value
between 0.90 and 1.10. It came in at 1.133, outside the range I named. The
interpolation on log-perplexity that produced 1.00 assumed the axis was monotone
between its two ends, and it is not.

## The axis is non-monotone, and the registration's reasoning about that was loose

The three point estimates run **1.055 → 1.133 → 0.906** as the base model gets
worse. That is non-monotone: the midpoint sits *above* the converged model.

The registration tied non-monotonicity to landing at or above 1.178, which was
imprecise. Any value above 1.055 makes the axis non-monotone, and 1.133 is above
1.055. The falsification criterion was still the right one to score against —
1.178 separates "with the parameter count" from "with the quality-matched model"
— but the sentence claiming that only a value ≥ 1.178 would imply
non-monotonicity was wrong when written, and is corrected here rather than left
standing.

**What rescues the reading is that no pair of the three separates:**

| pair | gap | combined bound | ratio |
| --- | ---: | ---: | ---: |
| ppl 23.5 vs ppl 39.4 | 0.078 | 0.167 | 0.46x |
| ppl 23.5 vs ppl 107 | 0.149 | 0.241 | 0.62x |
| ppl 39.4 vs ppl 107 | 0.227 | 0.230 | 0.98x |

All three are mutually indistinguishable at three seeds. The honest statement is
therefore **not** that the exponent rises and then falls with base-model quality;
it is that the exponent is flat across a 4.6-fold range of base-model perplexity
to within the resolution of this study, and that the non-monotone ordering of the
point estimates is not resolved by the data. The widest of the three gaps sits at
0.98x of its own bound — as close to separating as a pair can get without doing
so.

## The test this registration declined to make the criterion, and why it was right to

The registration argued in advance that a margin test against the quality-matched
30M model (1.300) was unsatisfiable and would be dishonest to register. Scored
anyway, now that it can be: the gap is 0.167 against a combined bound of 0.191,
or **0.87x — it does not separate.** The prediction would have failed on that
criterion even though H1 is true, exactly as the registration said. That is the
clearest vindication in this file, and it is of the *method* rather than of the
hypothesis.

## Secondary, which was not of record

*The optima.* Both rise as the base model gets worse, and the guess recorded in
advance was close on one arm and low on the other: the padded optimum was
expected near 4–6e-5 and came in at **5.22e-5**; the packed optimum was expected
near 2–2.5e-4 and came in at **2.85e-4**, above the range guessed. Against the
fully pretrained model the two arms rise 1.86x and 2.09x, sitting between that
model and the step_500 checkpoint's 4.00x and 3.20x. §4.7.1's finding — a
less-pretrained base model wants a larger rate in both arms while the distance
between them is left alone — holds at a third point on a much longer range.

*The grid.* It bracketed on the first pass at every seed. The advance guess about
where the optima would fall is what made that happen, and it is recorded above as
a guess rather than rewritten as a plan.
