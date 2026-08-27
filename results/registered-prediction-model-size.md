# Registered prediction: the second model size

**Written 2026-08-25 15:20 IST, before any small-model SFT run existed.**
Verified at authoring time that `results/lr_scaling_small.csv` did not exist, so
the ledger this is scored against held zero rows. The 30M pretrain was at step
15,090 of 18,000 and no fine-tune had been launched.

**Drafted by the assistant during the pretraining run and not yet reviewed by
the author.** It is recorded now because a prediction written after the runs
land is worth nothing; strike it before the sweeps finish if you disagree with
it, and it costs nothing.

This is the third registered prediction in this project. The first (§4.3) was
falsified and is reported as such; the second and third (scale, on Alpaca and
then on Dolly) were confirmed.

## Why this run exists

Every number in the paper is one model size. §5 says so, and it is the largest
open threat to the scale reading in §4.8: the exponent rises with the scale of
the *run* — the data and optimizer steps one epoch takes — and that was measured
only at 124M. If it is a property of the 124M model rather than of run scale,
the section's central claim does not generalise and should be stated as
model-specific.

The 30M model (`configs/model/small.yaml`, 29.9M parameters) is pretrained on
the same FineWeb-Edu corpus at a matched token-to-parameter ratio (~40x, 18,000
steps), so it is comparably trained for its size rather than matched on absolute
tokens.

## The comparison is matched, and this was checked in advance

The fine-tune window is fixed at `seq_len: 512` in the sweep's config template,
independent of the base model's context length. So the packing factors, the
per-step supervised-token counts and the cell step counts all carry over from
the 124M runs unchanged, and the only thing that differs between the two
factorials is the base model. Checked before registering, because a differing
window would have changed the packing ratio and made the comparison worthless.

| | 124M (measured) | 30M (predicted) |
| --- | ---: | ---: |
| `alpaca`, packing 4.47x | exponent **1.055 ± 0.128**, shift 4.86x | — |
| `alpaca_third`, packing 4.51x | exponent **0.670 ± 0.043**, shift 2.74x | — |
| gap (scale effect, 3x) | **0.385**, against a combined bound of 0.135 — 2.9x | — |

## The two hypotheses

| | predicts |
| --- | --- |
| **H1 — run scale governs, and it is not specific to the 124M model.** | At 30M, `alpaca`'s exponent again exceeds `alpaca_third`'s, by more than 0.135. |
| **H2 — the gap was a property of the 124M model.** | At 30M the two exponents differ by less than 0.135, in either direction. |

**H1 is the prediction of record**, and as with the Dolly replication the
commitment is *direction plus margin*, not the values. §4.8 is written as a
claim about the scale of the run; if that is what it is, changing the model
should not remove it.

**What would falsify it.** The two 30M exponents landing within 0.135 of each
other, or `alpaca_third` landing above `alpaca`. Either means §4.8's scale
reading has to be reported as 124M-specific.

## Secondary, and explicitly not of record

*The levels, as opposed to the gap.* Li et al. (2024) key the optimum on where
the batch sits relative to the gradient noise scale, and the noise scale is
generally smaller for a smaller model. The batches here are identical in
supervised tokens, so at 30M the same batch sits relatively *larger*, which on
that reading pushes toward the linear end. I would therefore guess both 30M
exponents come out at or above their 124M counterparts. This is a guess, not a
commitment: it rests on an assumption about the noise scale that nothing here
measures, and it does not discriminate H1 from H2.

*The floor.* All eight settings measured so far shift the optimum by at least
1.69x. I predict both 30M cells shift by more than 1.5x — that packing moves the
optimum at a second model size at all. A weak prediction, but a falsifiable one.

## How it will be scored

`scripts/analyze_packing_series.py --results results/lr_scaling_small.csv`,
which now takes that flag. Whichever hypothesis the printed exponents fall
nearer is the one that survives, with no discretion. If the gap lands near 0.135
neither is confirmed and that is the result.

## A limitation recorded in advance

`queue10.sh` runs seed 1337 only, so the 30M side will have **no seed bound of
its own** and every optimum will rest on one curve. The 0.135 margin above is
the 124M combined bound used as a yardstick, which is the best available and is
not symmetric. This is the same weakness the first prediction carried before it
was replicated, and it was worth flagging then: replication moved that number.
A seed pass over the three learning rates bracketing each 30M optimum is
required before any of this goes in the paper as more than provisional, and if
it moves the numbers this document will say so.

---

# Scored, 2026-08-26

**H1 confirmed.** Scored exactly as specified above, on the exponents
`scripts/export_exponents.py` writes to `results/exponents.csv`.

| | measured |
| --- | --- |
| 30M, `alpaca` | exponent **1.300 ± 0.159** |
| 30M, `alpaca_third` | exponent **0.768 ± 0.094** |
| gap | **0.531**, against the registered margin of 0.135 — 3.9x it |

The commitment was direction plus margin: `alpaca` above `alpaca_third` by more
than 0.135. It is above it by 0.531. H2 required the two to land within 0.135 of
each other, or `alpaca_third` to land higher; neither happened. §4.6's scale
reading is not 124M-specific.

## The limitation recorded in advance was addressed

The prediction flagged that the 30M sweep as queued ran seed 1337 only, and that
a seed pass over the three learning rates bracketing each optimum was required
before any of it went into the paper as more than provisional. That pass was
run. All six 30M cells bracket their optimum at all three seeds with none
dropped, spreads 1.06x to 1.23x, and the bound above (0.159 and 0.094) is
computed from them rather than borrowed from 124M. It did not move the verdict.

## The secondary guesses, neither of record

*The levels.* Guessed both 30M exponents would come out at or above their 124M
counterparts. Both did — 1.300 against 1.055 and 0.768 against 0.670. This was
explicitly not a commitment, and it should not be read as a confirmed
prediction: neither difference clears its own combined bound (1.2x and 0.9x),
and the reasoning behind the guess rests on an assumption about the gradient
noise scale that nothing here measures. What can be said is that a third model
size was added afterwards and continued the same direction (7M at 1.695 and
0.905), and that the direction now holds in all six pairwise comparisons while
clearing its bound in one.

*The floor.* Predicted both 30M cells would shift the optimum by more than 1.5x.
They shift by 7.01x and 3.18x. Held.

## What was learned that was not predicted

The 30M model was trained about twice as heavily for its size as the 124M model
— an error, recorded in `configs/train/fineweb_small.yaml` — so this comparison
confounded model size with pretraining budget. Nothing above anticipated that.
It was measured afterwards from the step-9,000 checkpoint (§4.7.1): halving the
budget moves both optima up by about 1.45x and moves the exponent between them
by 0.002 and 0.019, against bounds of 0.198 and 0.137. The confound is real and
its effect on this prediction's verdict is nil.
