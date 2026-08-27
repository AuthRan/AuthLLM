# Registered prediction: is section 4.7 measuring model size, or model quality?

**Written 2026-08-26, before any run of this comparison existed.** Verified at
authoring time that `results/lr_scaling_quality.csv` does not exist, so the
ledger this is scored against holds zero rows.

**Drafted by the assistant and not reviewed by the author before registration.**
Recorded now because a prediction written after the runs land is worth nothing.
Strike it if you disagree; that costs nothing.

This is the fifth registered prediction in this project. The first (§4.2) was
falsified and is reported as such; the second and third (scale, on Alpaca then
Dolly) and the fourth (scale survives a change of model) were confirmed.

## The problem

§4.7 reports that the packing exponent rises as the model shrinks: 1.055 at
124M, 1.300 at 30M, 1.695 at 7M, with the 17x span clearing its combined seed
bound by 2.2x. It is stated there as a claim about **model size**.

It is not only a claim about model size. The three models also differ in how
good they are — validation perplexity 23.5, 38.0 and 115.2 — because a smaller
model trained on the same corpus is a worse model. *Smaller* and *worse* are
perfectly confounded in that series, and nothing in §4.7 separates them. A
reader is entitled to ask whether the exponent tracks parameter count or simply
tracks how badly the base model models language.

§4.7.1 is the only existing evidence, and it is weak for this purpose. Taking
the 30M model at half its pretraining budget moves its perplexity from 38.0 to
43.9 and moves the exponent by 0.002 and 0.019, against bounds of 0.198 and
0.137 — no effect. But that is a 15% change in perplexity, and the gap this
question is about is a factor of five.

## The design

The 124M pretraining run checkpointed every 500 steps, and its perplexity
trajectory passes through both of the other models' final values:

| base model | perplexity | matches |
| --- | ---: | --- |
| `checkpoints/medium/step_500.pt` | 107.0 | 7M final (115.2) |
| `checkpoints/medium/step_2500.pt` | 39.4 | 30M final (38.0) |
| `checkpoints/medium/step_20000.pt` | 23.5 | 124M final, i.e. §4.7 itself |

Running the Alpaca whole-corpus factorial from these gives a **quality series at
a fixed parameter count**. The comparison of record is the extreme one:
`step_500`, a 124M model at the 7M model's quality.

Everything else is held: same architecture, same fine-tuning window, same
packing factor 4.47x, same cells (`unpacked_1600` and `packed_350`), same
estimator, same seeds.

## The two hypotheses

| | predicts for 124M @ step_500 |
| --- | --- |
| **H1 — §4.7 measures parameter count.** | The exponent stays near the 124M value of 1.055, and lands more than 0.291 below 7M's 1.695. |
| **H2 — §4.7 measures base-model quality.** | The exponent rises toward 7M's 1.695, landing within 0.291 of it. |

**H1 is the prediction of record.** 0.291 is the combined seed bound on the
124M-against-7M comparison that §4.7 already reports, used here as the yardstick
because the new cells have no bound of their own until they are replicated.

**Why H1.** §4.7.1 found the exponent indifferent to a change in pretraining
budget at fixed size, which is the same axis this tests over a longer range. If
quality drove the exponent, that null should not have been so flat.

**What would falsify it.** The `step_500` exponent landing within 0.291 of 1.695,
or above it. That would mean §4.7's model-size claim is a base-model-quality
claim, and the paper would have to be rewritten to say so — the model-size
framing in §4.7, the abstract and contribution 7 would all be wrong.

**A third outcome is possible and should not be forced into either box.** The
exponent landing between the two, clearing neither margin, means both size and
quality contribute and this design cannot apportion them. That is the honest
result if it happens, and it is likely enough that it is written down here in
advance rather than discovered as a disappointment.

## Secondary, explicitly not of record

*The middle point.* If `step_2500` (perplexity 39.4) is also run, H1 predicts it
too sits near 1.055 rather than near 30M's 1.300. Weaker: the 30M-against-124M
gap does not clear its own bound in §4.7 either, so this cannot discriminate
much.

*The optima themselves.* §4.7.1 found that a less-pretrained base model wants a
larger fine-tuning rate in both arms — about 1.45x for a halved budget — while
leaving the ratio alone. `step_500` is far less trained than that, so I expect
both its optima well above the final model's 2.81e-5 and 1.37e-4, possibly by
more than 3x. This is a guess about levels, not about the exponent, and it does
not discriminate H1 from H2. It matters operationally: the grid has to be
extended upward or the optima will not bracket.

## How it will be scored

`python scripts/export_exponents.py` after adding the ledger, then the
`124M@ppl107` row of `results/exponents.csv`, compared against 1.055 and 1.695
with the 0.291 margin. No discretion.

## Limitations recorded in advance

1. **An early checkpoint of a big model is not a converged small model.** At step
   500 of 20,000 the model is 2.5% through its cosine schedule, so its weights
   are mid-trajectory in a way a finished 7M model's are not. Matching on
   perplexity matches one number, not the state. This is the main reason the
   result should be read as *evidence about* the confound rather than as
   settling it.
2. **Perplexity is the wrong quality axis if the right one is something else.**
   The models are matched on held-out FineWeb-Edu loss, not on any downstream
   capability.
3. **One corpus, one packing factor.** Alpaca whole only.
4. **Seeds.** The comparison of record needs the same three-seed replication as
   everything else in Appendix C before it goes in the paper as more than
   provisional. If seed 1337 alone is what exists when this is written up, it
   must be labelled as such.
