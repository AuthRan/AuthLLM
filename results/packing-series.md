# The optimum against the packing ratio (final_val_loss)

| corpus | packing factor | lr* padded | lr* packed | shift | exponent | seed bound | seeds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| alpaca_short | 7.84x | 4.80e-05 | 1.39e-04 | **2.90x** | **0.517** | ±0.042 | 3 |
| alpaca_mid | 4.87x | 4.48e-05 | 1.33e-04 | **2.96x** | **0.686** | ±0.067 | 2 |
| alpaca_long | 2.73x | 6.73e-05 | 1.37e-04 | **2.03x** | **0.707** | ±0.265 | 3 |
| alpaca_ninth | 4.53x | 5.83e-05 | 1.04e-04 | **1.79x** | **0.385** | ±0.099 | 2 |
| alpaca_third | 4.51x | 4.59e-05 | 1.26e-04 | **2.74x** | **0.670** | ±0.043 | 3 |
| alpaca | 4.47x | 2.81e-05 | 1.37e-04 | **4.86x** | **1.055** | ±0.128 | 3 |
| dolly | 2.92x | 3.78e-05 | 7.84e-05 | **2.07x** | **0.681** | ±0.099 | 3 |
| dolly_third | 3.14x | 4.66e-05 | 7.87e-05 | **1.69x** | **0.456** | ±0.092 | 3 |

`shift` is how far the optimum moves between one packed epoch and one
padded epoch of the same corpus; `exponent` is that shift expressed against
the packing factor, so 1.0 is linear in supervised tokens per step and 0.5
is square-root. Inheriting a learning rate assumes a shift of 1.00x.

## Same corpus, different packing ratio

Holding the corpus fixed and splitting it by length, which moves the
packing ratio over 2.73x to 7.84x:

- 2.73x -> exponent **0.707**
- 4.87x -> exponent **0.686**
- 7.84x -> exponent **0.517**

a spread of 0.191. Against the combined seed bound on the two ends
(0.268) that is **0.7x** — inside what the seeds alone move it by. The packing ratio is *not*
established as moving the exponent: the trend across terciles is real in
the point estimates and absent once the seed spread is carried through.

## Different corpus, similar packing ratio

- `alpaca_long` at 2.73x -> 0.707 ± 0.265
- `dolly` at 2.92x -> 0.681 ± 0.099
- They differ by **0.026**, against a combined seed bound of 0.282 — **0.1x**.

  Two corpora at a similar packing ratio come out indistinguishable,
  which is the wrong way round for corpus identity setting the
  exponent. Read it as weak evidence: the pair is not matched on
  anything else either — their padded steps carry 3,709 and 2,272 supervised
  tokens and run 530 and 430 of them — and one of the two carries a
  seed bound wide enough to hide a real difference.

## Scale, with everything else matched

`alpaca_ninth` and `alpaca_third` are nested random samples of Alpaca, not
length terciles. All three rows below share a packing factor near 4.5x, a
padded step near 1,850 supervised tokens, and the same length distribution.
They differ in the size of the corpus and the number of optimizer steps one
epoch of it takes — which at a fixed data budget are the same quantity.

| corpus | training examples | padded steps | packing | exponent |
| --- | ---: | ---: | ---: | ---: |
| alpaca_ninth | 5,652 | 177 | 4.53x | **0.385** |
| alpaca_third | 16,956 | 530 | 4.51x | **0.670** |
| alpaca | 50,868 | 1600 | 4.47x | **1.055** |

Over a 9x span of scale the exponent moves **0.670**, monotonically rising.

With the packing factor, the padded batch and the length distribution all
held, changing only how much data the run sees moves the exponent by more
than the length terciles span. Scale is doing the work, and 4.7's reading
survives its own test.

## Scale, replicated on the second corpus

The ladder above is one corpus. This is the registered replication of it on
the other: a random third of Dolly against the whole of Dolly, the same ~3x
drop in scale. The prediction of record was directional — the exponent should
come out lower, by more than the combined seed bound.

| corpus | training examples | padded steps | packing | exponent |
| --- | ---: | ---: | ---: | ---: |
| dolly_third | 4,585 | 143 | 3.14x | **0.456** ± 0.092 |
| dolly | 13,756 | 430 | 2.92x | **0.681** ± 0.099 |

The third comes out **lower** by **0.225**, against a combined seed bound of 0.135 — **1.7x**.

**The prediction holds.** Both of its conditions are met: the direction is
down, and the margin clears the seeds. The scale reading is no longer a
single-corpus result — dropping scale by ~3x lowers the exponent on Dolly
as it does on Alpaca, and section 4.7 can rest on both.

The size of the drop does not transfer, though: 0.225 here against 0.385 on
Alpaca for the same 3x. Taking Alpaca's drop at face value would have put
this near 0.31; it landed at 0.456. The direction replicates and the
magnitude does not, which is what the prediction was worded to test and
what it was worded not to claim.

## Same packing ratio, same corpus, different scale

- `alpaca_mid` at 4.87x -> **0.686** (16,956 examples, 530 padded steps)
- `alpaca` at 4.47x -> **1.055** (50,868 examples, 1600 padded steps)

The packing ratios differ by 1.09x and the exponents by **0.369** — against a combined seed bound of
0.144, which is **2.6x**. One is drawn from the other, so this is
not a corpus difference either. What separates them is scale: three times
the data and three times the optimizer steps.

**So the exponent is not governed by the corpus, and the packing ratio is
not established as governing it either** — the tercile trend does not survive
its own seed spread. The one comparison that does clear its noise by a
comfortable margin is the scale one. That is where Li et al. (2024) put the
dependence, and it is what this design was not built to resolve.

The three Alpaca subsets are also nested within it, and splitting by length
changes more than the packing ratio. Held fixed across them: 32 examples per
step, 530 padded steps, 16,956 training examples. Moving with the ratio:
supervised tokens per padded step, which runs 411 to 3,709 — a ninefold
range — and the packed step count, and the mean response length that caused
all of it. Splitting a corpus by length cannot vary its packing ratio alone,
because the packing ratio *is* a function of the lengths.

So `exponent tracks the packing ratio` is not established here; what is
established is that it is not a constant and not fixed by corpus identity.
A ninefold change of batch size is itself a candidate: Li et al. (2024)
predict the optimal learning rate is non-monotone in batch size, so the
local exponent should depend on where a batch sits relative to the gradient
noise scale. Separating that from the packing ratio needs a design that
holds the padded batch fixed while the ratio moves, which this is not.

## What every row agrees on

Every corpus and every packing ratio moves the optimum by at least **1.69x** (dolly_third), against the 1.00x that
inheriting a learning rate assumes. Whatever governs the size of the shift,
its existence is not in question.
