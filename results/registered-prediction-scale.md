# Registered prediction: the scale control

**Written before any `alpaca_third` run finished.** Verified at authoring time
that `results/lr_scaling_sweep.csv` held zero rows for that dataset. This is the
second registered prediction in this project; the first (section 4.3 of the
paper) was falsified, and is reported as such.

## What is being predicted

`alpaca_third` is a random third of Alpaca. Against the whole corpus it is
matched on packing factor (4.51x against 4.47x), on supervised tokens per padded
step (1,841 against 1,888) and on length distribution. It differs only in the
scale of the run: 16,956 training examples and 530 padded steps against 50,868
and 1,600.

Section 4.7 concluded that the matched-budget exponent is fixed by neither the
corpus nor the packing ratio, and that what moves it is scale. That conclusion
was reached by elimination, not by test. This is the test.

## The two hypotheses, and what each predicts

| | predicted exponent | predicted shift |
| --- | ---: | ---: |
| **H1 — scale governs.** It lands with the terciles, which share its size and step count. | ~0.67 | 4.51^0.67 = **2.77x** |
| **H2 — scale does not.** It lands with the whole corpus, which shares its packing ratio, batch and length distribution. | ~1.055 | 4.51^1.055 = **5.06x** |

The two are far apart — a factor of 1.8 in the shift — so this discriminates.

**H1 is the prediction of record.** Section 4.7 as written commits to it.

## A separate test of the paper's own advice

Section 6 tells a practitioner to bracket the packed optimum in
`[lr_pad * sqrt(p), lr_pad * 1.2p]`. For p = 4.51 that is
`[2.12 * lr_pad, 5.41 * lr_pad]`. Both hypotheses fall inside it, so this does
not discriminate between them — it is an out-of-sample test of the
recommendation itself, which has so far been checked only on the five settings
it was drawn from.

## How it will be scored

`scripts/analyze_packing_series.py` reports the exponent. No discretion: the
number it prints is the number this is scored against, and whichever hypothesis
it falls nearer is the one that survives. If it lands between the two — say
0.80 to 0.90 — neither is confirmed and that is the result.

---

# Second registered prediction: does scale replicate on the other corpus?

**Added before any `dolly_third` run finished** — verified zero rows for that
dataset in the results file at authoring time.

## Why

After seed replication, scale is the *only* comparison in section 4.7 that
clears its own noise: Alpaca's middle third and the whole corpus sit at the same
packing ratio and differ by 0.369 in exponent against a combined seed bound of
0.144. Everything else — corpus identity, packing ratio — is either ruled out or
unsupported. A single surviving comparison inside a single corpus is thin
evidence for the claim the section now rests on, so it needs a replication on
the second corpus.

`dolly_third` is a random third of Dolly: 4,585 training examples against
13,756, and 143 padded steps against 430. A ~3x drop in scale, the same span as
Alpaca's middle-third-versus-whole comparison.

## The prediction

**Directional, and that is the whole test.** If scale governs the exponent, then
dropping scale by 3x should lower it, as it did on Alpaca:

| | exponent |
| --- | ---: |
| Dolly, whole (13,756 examples, 430 steps) | 0.681 ± 0.099 (measured) |
| `dolly_third` (4,585 examples, 143 steps) | **predicted lower**, and by more than the combined seed bound |

Taking Alpaca's drop at face value (-0.369 for the same 3x) would put
`dolly_third` near **0.31**, but that is a point estimate from one corpus and
the prediction of record is the direction plus the margin, not the number.

**What would falsify it.** `dolly_third` landing at or above 0.681, or below it
by less than the combined seed bound. Either outcome means the scale reading
does not replicate across corpora, and section 4.7's surviving claim would have
to be reported as Alpaca-only.

## Caveat recorded in advance

The two are less cleanly matched than the Alpaca pair: `dolly_third` packs at
3.14x against the whole corpus's 2.92x, and its padded step carries 2,126
supervised tokens against 2,272, both from sampling variation in a smaller draw.
The exponent is computed against each corpus's own packing factor, which handles
the first; the second is a real if small mismatch in batch size, in the
direction of a slightly *smaller* batch for the third.

---

# Outcome of the first prediction

**Scored on `scripts/analyze_packing_series.py`'s output, as specified.**

| | exponent | shift |
| --- | ---: | ---: |
| H1 — scale governs | ~0.67 | 2.77x |
| H2 — scale does not | ~1.055 | 5.06x |
| **`alpaca_third`, measured** | **0.685** | **2.80x** |

**H1 confirmed.** The measurement lands 0.015 from H1 and 0.370 from H2, and the
predicted shift of 2.77x against a measured 2.80x is closer than the prediction
deserved — the exponent was quoted to two figures from a different subset.

The comparison this was built for: `alpaca_third` against the whole corpus,
matched on packing factor (4.51x against 4.47x), on supervised tokens per padded
step (1,841 against 1,888) and on length distribution, differing only in scale.
Exponents 0.685 against 1.055 — a gap of 0.371 against a combined seed bound of
0.128, or 2.9x.

A second thing worth recording, unplanned. `alpaca_mid` — the middle length
tercile — came out at 0.686. It shares `alpaca_third`'s size (16,956 examples)
and step count (530) but not its packing ratio (4.87x against 4.51x) or its
length composition (a tercile against a random sample). The two agree to
**0.001**. Two subsets matched on scale and differing in composition and packing
ratio land on the same exponent; a subset and its parent corpus matched on
composition and packing ratio and differing in scale do not. That is as clean a
separation as this design can produce.

**The bracket held out of sample.** Section 6 predicts the packed optimum lies
in `[lr_pad * sqrt(p), lr_pad * 1.2p]` = `[9.40e-5, 2.40e-4]`. Measured:
1.24e-4, comfortably inside, on a setting the bracket was not drawn from.

**One caveat.** `alpaca_third` is one seed as scored here, so its own bound is
zero and the 2.9x above rests on the whole corpus's bound alone. Replication is
queued; if it moves the number this section will say so.

**The replication landed, and it moved the number — toward the prediction.**
Seeds 1338 and 1339 put `alpaca_third` at **0.670 ± 0.043** (shift 2.74x),
against 0.685 at seed 1337 alone. H1 predicted ~0.67 and a shift of 2.77x, so
the scored value went from 0.015 away to **0.000 away**; H2 is now 0.385 away.
The confirmation stands and is stronger than when it was scored.

The caveat itself is discharged: the cell now carries three seeds and its own
bound, so the comparison against the whole corpus no longer rests on one side's
noise. Recomputed, the gap is 0.385 against a combined bound of 0.135 — **2.9x**,
unchanged in substance from the single-seed reading.

The unplanned agreement in the same section is worth restating honestly, because
replication cost it its headline precision: `alpaca_mid` at 0.686 against
`alpaca_third` at 0.670 now agree to **0.016** rather than 0.001, against bounds
of ±0.067 and ±0.043. That is still a close agreement and it still makes the
point — two subsets matched on scale, differing in packing ratio and
composition, landing together — but 0.001 was the seeds flattering us, and
§4.8's caution about sub-noise precision applies to it as much as to the
straight line it was written about.

---

# Outcome of the second prediction

**Scored on `scripts/analyze_packing_series.py`'s output, as specified.** The
corpus was added to that script's tables for the scoring; the exponent it
reports is the number below.

| | exponent | shift |
| --- | ---: | ---: |
| Dolly, whole (13,756 examples, 430 steps) | 0.681 ± 0.099 | 2.07x |
| **`dolly_third`, measured** (4,585 examples, 143 steps) | **0.456 ± 0.092** | **1.69x** |

**Confirmed, on both of the conditions it was written with.** The prediction was
that dropping scale ~3x would move the exponent *down*, and by *more than the
combined seed bound*. It came out lower by **0.225** against a combined bound of
**0.135** — a ratio of **1.7x**. Neither falsification condition fired: it did
not land at or above 0.681, and the gap did not fall inside the bound.

So the scale reading is no longer a single-corpus result. It was the only
comparison in section 4.7 to clear its own noise, and it rested entirely on
Alpaca; it now holds on both corpora, and the section can rest on the pair.

**The magnitude did not transfer, and the prediction said it would not have to.**
Alpaca's exponent falls 0.385 over the same 3x drop; Dolly's falls 0.225. Taking
Alpaca's drop at face value would have put this near 0.31 and it landed at 0.456
— which is why the prediction of record was the direction plus the margin rather
than the number. Read positively: scale moves the exponent on both corpora, and
how *much* it moves is not a corpus-independent constant. That is a weaker claim
than a shared scaling law and it is the one the data supports.

**A consequence for the floor.** `dolly_third` at 1.69x is now the smallest
shift anywhere in the series, below `alpaca_ninth`'s 1.79x. The claim that every
corpus and every packing ratio moves the optimum by at least ~1.7x is unchanged
in kind — no cell has yet come near the 1.00x that inheriting a learning rate
assumes — but the floor is set by the smallest runs, and both of the two lowest
are the smallest-scale cells in the study. That is consistent with the scale
reading rather than a separate fact about them.

**Caveats.** Both cells behind the Dolly pair carry three seeds, so unlike the
first prediction this one does not rest on a single-seed bound. The mismatch
recorded in advance stands: `dolly_third` packs at 3.14x against the whole
corpus's 2.92x, and its padded step carries 2,126 supervised tokens against
2,272. The exponent is computed against each corpus's own packing factor, which
handles the first. The second is a real if small difference in batch size, and
it runs in the direction of a *smaller* batch for the third — which on the
Li et al. (2024) reading would push its exponent down on its own, so some
unknown part of the 0.225 is batch rather than scale. Separating them needs the
padded batch held fixed while scale moves, which no cell here does.

## The paper's own advice, tested again — and falsified

The first prediction recorded a separate, non-discriminating test: §6 tells a
practitioner to bracket the packed optimum in `[lr_pad * sqrt(p), lr_pad * 1.2p]`,
and `alpaca_third` landed comfortably inside it. Applying the same check to the
two settings run since:

| setting | examples | p | floor `lr_pad*sqrt(p)` | packed optimum | result |
| --- | ---: | ---: | ---: | ---: | :--- |
| `alpaca_third` | 16,956 | 4.51x | 9.75e-05 | 1.26e-04 | inside |
| `dolly_third` | 4,585 | 3.14x | 8.27e-05 | 7.87e-05 | **5% below floor** |
| `alpaca_ninth` | 5,652 | 4.53x | 1.24e-04 | 1.04e-04 | **16% below floor** |

**The bracket is falsified out of sample**, on the lower bound, on the two
smallest-scale settings in the study and on no others. §6 had warned in advance
that "a sixth setting could fall outside"; the sixth was fine and the seventh and
eighth were not.

This is not an unrelated miss. The floor asserts an exponent of at least 0.5,
and the scale result immediately above says the exponent falls as the run gets
smaller. A floor that is scale-free sitting under an exponent that is not was
always going to fail at the bottom of the range, and it did. Every setting at
13,756 examples or more clears it (exponents 0.517 to 1.055); both settings
under 6,000 examples miss it (0.385 and 0.456).

§6 now carries the scale condition and an amended floor of `p^0.35` below about
ten thousand examples. That amendment is fit to the two points that broke the
original and has not itself been tested out of sample — it is recorded as a
patch, not as a finding, and the next small-scale setting is its test.

---

# Outcome of the second prediction

| | exponent | scale |
| --- | ---: | ---: |
| Dolly, whole | 0.681 ± 0.099 | 13,756 examples, 430 steps |
| **`dolly_third`, measured** | **0.456 ± 0.092** | 4,585 examples, 143 steps |

**Confirmed.** The prediction was directional — lower, by more than the combined
seed bound — and both halves hold: the gap is 0.225 against a combined bound of
0.135, or 1.7x. Three seeds each. The scale reading is not Alpaca-only.

Alpaca's 3x scale step moved the exponent 0.371 and Dolly's moved it 0.225. Both
positive, both clearing their bounds, on corpora that differ in size, packing
ratio and provenance.

# A retraction, recorded here because it was nearly a finding

Before the scale series was replicated, its three points sat exactly 3x apart in
scale with exponent differences of +0.369 and +0.371 — a straight line in
log(scale) with residuals of ±0.0006. It was reported at the time as *not* a
law, on the grounds that residuals two orders of magnitude below the seed bounds
(±0.04 to ±0.13) are coincidence rather than signal.

Replication settles it. The three points are now 0.385, 0.670 and 1.055, with
differences of **+0.285 and +0.385**. The straight line is gone. What survives is
what was claimed at the time: a monotone increase with scale, with both steps
clearing their bounds (2.6x and 2.9x), and no functional form.
