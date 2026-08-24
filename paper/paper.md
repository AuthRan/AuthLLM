# Does Sequence Packing Change the Optimal Learning Rate?

**Status: draft, complete through §7. Both factorials are complete (98 runs),
every cell carries the full seven-point grid, and every cell the headline rests
on is seed-replicated. Remaining: a venue decision, and author list /
affiliation for submission.**

---

## Abstract

Sequence packing is standard practice in supervised fine-tuning and is presented
as an efficiency change that leaves the model untouched: with the right masking
and position ids, a packed example is mathematically identical to the same
example run alone. The identity holds per example, not per optimizer step, and
the packing literature nonetheless advises inheriting the learning rate — advice
we can find asserted but never swept. We sweep it: 98 runs over a 2x2 factorial
of batch size and step count, on two instruction corpora at packing factors of
4.5x and 2.9x.

Inheriting the learning rate gives up most of what packing offers.
Retuning is worth 0.050 nats on one corpus and 0.018 on the other against
inheriting. Measured against the padded baseline the rate was inherited from,
the packed run at its own optimum wins on both corpora, while the packed run at
the inherited rate wins on neither: a 0.004-nat wash on Alpaca, and 0.015 nats
*worse* than not packing at all on Dolly. Turning packing on without retuning
buys throughput and returns, at best, nothing on quality. Packing's effect on
quality has the sign of its learning rate, not of packing.

We also show that the shift is not a pure batch-size effect, though it is easily
read as one. At a fixed data budget, packing raises the batch size and lowers the
optimizer step count by the same factor, so the ~5x shift observed between one
packed epoch and one padded epoch is equally consistent with a rule containing
no batch-size term. Separating the two factorially, both contribute — but the
batch exponent is 0.67 on one corpus and 0.44 on the other, so no single scaling
rule is supported by these data. A prediction registered from the first corpus
before the second was run missed by 1.28x, about 2.9 times the seed spread on
the cells that define it; we report the falsification rather than fit a law to
two points. We further identify a methodological trap the design exposes: at a
matched step count the packed arm accumulates more epochs, and its apparent
optimum is then set by which learning rate overfits least rather than by step
size.

All results are at 124M parameters on two corpora, and should not be
extrapolated to production batch sizes without further points.

## 1. Introduction

Sequence packing — concatenating several short training examples into one
fixed-length window, with attention masked at the boundaries so they cannot
see each other — is standard practice in supervised fine-tuning. Its appeal is
arithmetic: instruction-tuning corpora are short relative to the context
window, so a padded batch spends most of its compute on padding. In the setting
we study, an Alpaca example averages 113 tokens in a 512-token window and only
~58 of those tokens are supervised, so roughly 89% of every optimizer step is
spent on positions that contribute nothing to the loss. Packing recovers most
of it: we measure 4.40x the supervised tokens per second at 1.02x the per-step
cost.

Packing is usually presented as an efficiency change that leaves the model
untouched — with the right masking and position ids, a packed example is
mathematically identical to the same example run alone. That identity holds
per example. It does not hold per optimizer step. A packed step in our setting
carries 4.5x the supervised tokens of a padded one, which makes it a different
step in exactly the way a larger batch is a different step, and the question
this paper asks is whether the learning rate has to move with it.

The practical literature says no. Krell et al. (2021) advise explicitly
against scaling the learning rate with the batch size under packing, reporting
that it slowed convergence, and instead propose adjusting LAMB's decay
parameters by the packing factor. The recent packing study of Wang et al.
(2025) states that "in packing mode, the batch size is no longer directly
proportional to the learning rate," and holds the learning rate at 1e-5 across
both padded and packed runs of LLaMA-3-8B and 70B. Neither swept the learning
rate under packing. To our knowledge no published work does, which leaves the
standard advice — inherit the learning rate — resting on an assertion rather
than a measurement.

We sweep it. The measurement is complicated by a confound that is easy to miss
and that we ourselves initially missed: at a fixed data budget, packing changes
the number of optimizer steps in an epoch by the same factor it changes the
batch size, in the opposite direction. Any learning-rate shift observed between
"one packed epoch" and "one padded epoch" is therefore explained equally well
by a rule that has nothing to do with batch size at all. We resolve it with a
2x2 factorial that varies batch size and step count independently.

**Contributions.**

1. The first learning-rate sweep, to our knowledge, of packed against padded
   supervised fine-tuning, with padded controls at matched learning rates that
   rule out the alternative that packing merely repairs an untuned baseline.
2. A 2x2 factorial that separates the batch-size effect of packing from the
   step-count effect it induces at a fixed data budget — a confound that, left
   in, supports a conclusion the data does not.
3. A measurement of what the standard advice costs. On both corpora, packing at
   the inherited learning rate fails to beat the padded baseline it inherited
   from — a 0.004-nat wash on Alpaca, a 0.015-nat regression on Dolly — while
   packing at the retuned rate beats that baseline on both. Packing's effect on
   quality has the sign of its learning rate, not of packing.
4. Evidence that the shift is not one exponent. The batch effect at matched step
   counts is 2.73x on Alpaca (exponent 0.670) and 1.60x on Dolly (0.440), and a
   prediction registered from the first before running the second missed by
   about three times the seed spread. We report the failure and what we think
   drives it rather than fitting a law to two points.

## 2. Related work

**Sequence packing.** Krell et al. (2021) introduce packing without
cross-contamination for BERT, with the block-diagonal attention masking that
makes a packed example equivalent to an unpacked one, and address the
hyperparameter question directly. Their recommendation is to reduce the
computational batch size by the packing factor and otherwise change nothing;
where the batch size is kept, they propose the LAMB heuristic
`b1 := b1^p, b2 := b2^p` for packing factor `p`, and they advise against
learning-rate scaling on the grounds that it slowed convergence in their
experiments. Their setting is BERT pretraining with LAMB, not AdamW
fine-tuning, and they report no learning-rate sweep.

Wang et al. (2025) study packing for supervised fine-tuning at 8B and 70B
across four instruction corpora, finding that packing generally matches or
beats padding and that the gap widens with model and dataset size. They keep
the learning rate fixed at 1e-5 for both arms and note that packing breaks the
proportionality between batch size and learning rate, attributing this to
packing not holding the number of conversations per batch constant. The claim
is not accompanied by a sweep.

**Learning rate and batch size.** The linear scaling rule is standard for SGD
(Goyal et al., 2017). For adaptive optimizers the picture is contested.
Malladi et al. (2022) derive a square-root rule for Adam from an SDE
approximation, matching the empirical practice of scaling by sqrt(B) in BERT
training. Li et al. (2024) show the optimal learning rate for Adam is not
monotone in batch size at all: it rises and then falls, peaking at the gradient
noise scale `B_noise`, and reduces to square-root scaling in the small-batch
regime `B << B_noise`. Others have contested the square-root rule directly,
reporting that a batch-size-invariant formulation of AdamW is better matched by
linear scaling. The batch sizes in this paper are small in absolute terms —
1,888 and 8,444 supervised tokens per optimizer step — which places us in the
regime where Li et al. predict square-root behaviour, and makes any departure
from it worth reporting rather than assuming.

We are not aware of prior work that measures where the optimum actually sits
when the batch size is changed *by packing* rather than by the number of
sequences, which is the case where the two quantities that could govern the
rule — sequences per step and supervised tokens per step — come apart.

## 3. Method

### 3.1 Setup

All runs fine-tune a 124M-parameter decoder-only transformer (RoPE, RMSNorm,
SwiGLU, 12 layers, 12 heads, width 768), pretrained from scratch on 2.46B
tokens of FineWeb-Edu, on one of two instruction corpora:

| corpus | training examples | held out | dropped as over-length | supervised tokens/example |
| --- | ---: | ---: | ---: | ---: |
| Alpaca | 50,868 | 1,039 | 67 | 59 |
| Dolly | 13,756 | 300 | 955 | 71 |

The held-out split is 2% of the corpus, drawn before the over-length filter,
reconstructed from the training seed and seen by no run. Dolly loses far more
examples to the 512-token window than Alpaca does, and the same property —
longer examples, so fewer of them per window — is what gives it the smaller
packing factor that makes it a useful second point.

Every run: window 512, micro-batch 8, gradient accumulation 4, AdamW
(b1 = 0.9, b2 = 0.95, weight decay 0.1), gradient clipping at 1.0, fp16
autocast, cosine schedule from `max_lr` to `max_lr/10` over a warmup of 6.25%
of the schedule. The only quantities that vary across the grid are the corpus,
the packing flag, the step count, the peak learning rate, and the seed.
Hardware is a single RTX 2080 Ti per run.

Packed batches use a block-diagonal attention mask keyed on per-example
segment ids and per-example RoPE positions restarting at zero, so a packed
example produces logits identical to the same example run alone; this
equivalence is asserted by a unit test that compares logits and gradients with
and without the mask, including through gradient checkpointing.

**Loss normalization.** The objective is token-mean cross-entropy over
supervised positions (`ignore_index` on prompt and padding). Packing therefore
changes the *number* of terms averaged, not the scale of the gradient — it
reduces gradient noise without altering step size. This matters for
interpretation: the effect we measure is a batch-size effect and not a
disguised change in effective learning rate, which it would be under a
sum-reduced or per-sequence-normalized loss.

**Seeds and the validation split.** The fine-tuning script derives its held-out
split by shuffling the corpus with the same seed it trains with, so two seeds
score on different validation examples. The resulting offset is large — ~0.10
nats between seeds 1337 and 1338 on Dolly — and it is shared by every learning
rate within a seed, so it cancels out of that seed's argmin but not out of a
pointwise average across seeds. How tightly it is shared is measurable here:
across the nine Dolly points run at both seeds — three cells, learning rates
spanning 7.5x — the 1337-to-1338 gap is 0.1007 nats with a total range of
0.0025. The two seeds' curves are translates of one another to within a
fortieth of the offset between them, which is exactly why solving each seed
separately and aggregating the optima is the right operation and averaging
losses pointwise is the wrong one. Because the replication seeds were run only on
the three learning rates bracketing each argmin, averaging pointwise would drag
exactly those points and confirm the argmin that selected them. We therefore
solve each seed's curve for its own optimum and report the geometric mean and
the spread across seeds, never a seed-averaged loss.

**Evaluation.** Validation is computed on *unpacked* batches in both arms, so a
single ruler covers the whole grid. We report held-out cross-entropy on the
response tokens only. Because every run completes a full cosine cycle, the
endpoint is the comparable quantity, and we report the final validation loss as
the primary metric with the best-during-run value alongside it.

### 3.2 The confound, and the design that resolves it

Packing multiplies supervised tokens per optimizer step by the packing factor
`p` (here 4.47x, from 1,888 to 8,444). This is the ratio of *supervised tokens*,
which is slightly smaller than the 4.53x ratio of windows because packed windows
are not perfectly full; the token ratio is the one the batch effect is defined
against, and we use it throughout. At a fixed data budget of one epoch it
also divides the number of optimizer steps by approximately the same factor
(1,600 padded steps to 350 packed). Any comparison of "one packed epoch"
against "one padded epoch" therefore moves two variables at once, and two
different rules predict the same observed shift in the optimum:

- **Batch scaling.** `lr*` is governed by supervised tokens per step, so
  `lr*` should rise by ~4.47x (linear) or ~2.11x (square-root) under packing,
  independent of how many steps are run.
- **Schedule integral.** `lr*` is governed by 1/steps, so that the area under
  the schedule `max_lr x steps` is conserved, independent of batch size.

On the two cells that have historically been run — padded at 1,600 steps and
packed at 350 — these are indistinguishable. Both predict roughly a 4.5x
increase, and the numbers this project originally measured (3.0e-5 padded,
~1.5e-4 packed) fit both: 3.0e-5 x 1600 = 0.048 against 1.5e-4 x 350 = 0.0525,
within 10%.

They come apart off the diagonal. We therefore run the full factorial:

| | 350 steps | 1,600 steps |
| --- | --- | --- |
| **padded** (1,888 supervised tokens/step) | new | known |
| **packed** (8,444 supervised tokens/step) | known | new |

Batch scaling predicts the optimum depends on the row only; schedule integral
predicts it depends on the column only. Holding the step count fixed while
varying batch size means the smaller batch sees proportionally less data — the
padded 350-step cell is 0.22 of an epoch — which is not a defect of the design
but the definition of a larger batch: more data consumed per update. The
packed 1,600-step cell conversely runs 4.50 epochs, where overfitting rather
than the learning rate may set the minimum; we report the full validation
curve for every run so that this is visible rather than hidden inside a single
endpoint.

### 3.3 Grid

Learning rates {1.0, 2.0, 3.0, 6.0, 9.0, 15.0, 25.0} x 1e-5 in every cell —
seven points at 1.5-1.7x spacing over a 25x span, chosen so that both
hypotheses' predicted optima for the two new cells fall in the interior with
room on either side. An optimum landing on an edge is reported as unbracketed
rather than as a value.

Each cell's optimum is estimated by fitting a parabola in log(lr) through the
grid argmin and its two neighbours, which is the local shape of a
loss-versus-log-learning-rate curve near its minimum; the grid argmin is
reported beside it.

*(Seeds: the grid is first run at seed 1337 to locate the optima, then repeated
at additional seeds on the points bracketing each optimum. Seed count is
reported in §4 with the results it covers.)*

## 4. Results

### 4.1 The harness reproduces the original measurement

Nine of the grid's learning rates were run in this project before, under the
hand-written configs of section 10.6. Eight reproduce to four decimals and the
ninth differs by 0.0001:

| cell | 2.0e-5 | 3.0e-5 | 6.0e-5 | 9.0e-5 | 1.5e-4 |
| --- | ---: | ---: | ---: | ---: | ---: |
| packed, 350 steps — prior | 2.0912 | 2.0678 | 2.0352 | 2.0217 | 2.0175 |
| packed, 350 steps — here | 2.0911 | 2.0678 | 2.0352 | 2.0217 | 2.0175 |
| padded, 1,600 steps — prior | 2.0745 | 2.0720 | 2.0973 | — | — |
| padded, 1,600 steps — here | 2.0745 | 2.0720 | 2.0973 | — | — |

The sweep is therefore measuring the same quantity the original tables did, on
generated configs, and the two new cells are directly comparable to the two old
ones.

### 4.2 The full factorial

**Alpaca.** Final validation loss, seven learning rates per cell:

| cell | 1e-5 | 2e-5 | 3e-5 | 6e-5 | 9e-5 | 1.5e-4 | 2.5e-4 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| padded, 350 | 2.1885 | 2.1539 | 2.1397 | **2.1338** | 2.1466 | 2.1907 | 2.2770 |
| padded, 1,600 | 2.0973 | 2.0745 | **2.0720** | 2.0973 | 2.1335 | 2.2091 | 2.3149 |
| packed, 350 | 2.1353 | 2.0911 | 2.0678 | 2.0352 | 2.0217 | **2.0175** | 2.0319 |
| packed, 1,600 | 2.0421 | **2.0171** | 2.0197 | 2.0845 | 2.1789 | 2.3616 | 2.5703 |

Every optimum is bracketed — an interior minimum with a rise on both sides —
including the packed 350-step cell, which section 10.6 swept only as far as
1.5e-4 and could not bracket. Interpolating in log(lr):

| lr* | 350 steps | 1,600 steps |
| --- | ---: | ---: |
| **padded** | 5.00e-5 | 2.81e-5 |
| **packed** | 1.37e-4 | 2.35e-5 |

(Optima after seed replication; see 4.6. At seed 1337 alone they are 4.77e-5,
2.65e-5, 1.30e-4 and 2.25e-5.)

**Dolly.** The same grid, with the step counts that make one packed epoch (136)
and one padded epoch (430):

| cell | 1e-5 | 2e-5 | 3e-5 | 6e-5 | 9e-5 | 1.5e-4 | 2.5e-4 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| padded, 136 | 2.8277 | 2.8043 | 2.7931 | **2.7868** | 2.7978 | 2.8372 | 2.9178 |
| padded, 430 | 2.7801 | 2.7588 | **2.7517** | 2.7567 | 2.7765 | 2.8253 | 2.9180 |
| packed, 136 | 2.8071 | 2.7799 | 2.7662 | 2.7505 | **2.7484** | 2.7604 | 2.7973 |
| packed, 430 | 2.7596 | **2.7429** | 2.7430 | 2.7824 | 2.8433 | 2.9742 | 3.1525 |

| lr* | 136 steps | 430 steps |
| --- | ---: | ---: |
| **padded** | 4.89e-5 | 3.78e-5 |
| **packed** | 7.84e-5 | 2.44e-5 |

(At seed 1337 alone: 4.87e-5, 3.61e-5, 7.98e-5 and 2.44e-5. The packed 430-step
cell is seed 1337 only, being excluded from the headline as
overfitting-contaminated; the other three are three-seed aggregates.)

All eight optima across both corpora are bracketed.

![Learning-rate curves for both corpora](../resources/plots/06-lr-scaling-packing.png)

*Held-out loss against peak learning rate, seed 1337, minima marked. Dashed is
padded, solid is packed. The figure is one seed because the held-out split is
seeded with the training seed (3.1); the optima in the tables are aggregated
across seeds, each solved on its own curve.*

### 4.3 The second corpus, and a prediction that failed

Section 4.2's exponents were fitted on Alpaca. Dolly is the out-of-sample test:
a different corpus, a packing factor of 2.92x in supervised tokens per step
(568 to 1,658 per micro-batch, measured — note this is *not* the 3.16x ratio of
windows, and the token ratio is what the batch effect is defined against).

Before any Dolly run finished, the Alpaca batch exponent as it then stood
(0.673, and 0.670 after seed replication) was recorded as predicting a Dolly
batch effect of 2.92^0.673 = **2.06x**.

The measured value is **1.60x**, an exponent of **0.440** (three seeds).

The prediction fails, and it fails in an informative direction: 1.60x is close
to the square-root rule's 1.71x, while Alpaca's 2.73x sits well above its own
square-root prediction of 2.11x.

The failure is larger than the noise. Both cells defining Dolly's batch effect
were run at three seeds, with max/min spreads of 1.08x and 1.03x; combined
geometrically, that is about 1.09x of seed uncertainty on their ratio. The
prediction misses by 1.28x — in log terms, about **2.9 times that spread**.
Three seeds give a range and not a standard error, so this is a claim about
magnitude rather than a significance test; but the miss is several times the
scatter we can actually see, which is what separates a failed prediction from a
noisy one.

We therefore do not fit a shared power law. Two corpora, each with a
well-bracketed and seed-stable optimum, disagree on the exponent by more than
their noise, and the honest conclusion is that the batch exponent under packing
is corpus-dependent at this scale.

| | Alpaca | Dolly |
| --- | ---: | ---: |
| packing factor (supervised tokens/step) | 4.47x | 2.92x |
| batch effect at matched steps | 2.73x | 1.60x |
| — as an exponent | **0.670** | **0.440** |
| step effect, padded | 1.78x | 1.29x |
| — as an exponent | 0.379 | 0.224 |
| matched data budget (one epoch each) | 4.86x | 2.07x |
| — as an exponent | **1.055** | **0.681** |
| worst per-cell seed spread | 1.17x | 1.11x |

We do not think the honest reading of this is that one corpus is anomalous. The
fixed-step comparison that defines the batch effect necessarily varies data
seen as well — at a fixed step count the larger batch consumes proportionally
more of the corpus — and that residual confound grows with the packing factor,
which is larger for Alpaca. Some part of the exponent gap is therefore built
into the design rather than into the models. Li et al.'s (2024) non-monotone
picture supplies another candidate: a local exponent that depends on where a
batch size sits relative to the gradient noise scale need not be shared by two
corpora with different example lengths.

Either way, the conclusion is the same: **the shift is real and large on both
corpora, and it is not described by one exponent.**

### 4.4 What does replicate

Three things hold on both corpora.

**The optimum moves a long way at a matched data budget** — 4.86x on Alpaca and
2.07x on Dolly, against packing factors of 4.47x and 2.92x. Neither is close to
the 1.0x that inheriting a learning rate assumes, and both are many times the
seed spread.

**Inheriting the learning rate gives up most of what packing offers.** Taking
each corpus's padded optimum — 3e-5 on both — and applying it unchanged to the
packed run at the same data budget:

| | padded at its optimum | packed, inherited | packed at its optimum |
| --- | ---: | ---: | ---: |
| Alpaca | 2.0720 (3e-5) | 2.0678 (3e-5) | **2.0175** (1.5e-4) |
| Dolly | 2.7517 (3e-5) | 2.7662 (3e-5) | **2.7484** (9e-5) |

Retuning is worth **0.050 nats on Alpaca and 0.018 on Dolly** against
inheriting. Measured against the padded baseline instead, the packed run at its
own optimum wins on both corpora — by 0.054 and 0.003 nats — while the packed
run at the inherited rate wins on neither: 0.004 nats better than padding on
Alpaca, which at this scale is a wash, and 0.015 nats *worse* on Dolly. A
practitioner who turns packing on and changes nothing else buys throughput and
collects, at best, none of the quality packing had available; on one of the two
corpora they pay for it.

The Alpaca column is where this effect is easiest to overstate, so it is worth
reading closely. One grid point below the inherited rate, at 2e-5, the packed
run scores 2.0911 against padding's 2.0745 — a clear regression. The sign of
the inherit-versus-padding comparison therefore turns over between two adjacent
grid points, which is why we call it a wash on Alpaca rather than a regression,
and why the claim we would defend is the 0.050 nats that retuning recovers and
not the sign of a difference an order of magnitude smaller. For a change
routinely described as pure throughput, that is still the result: packing's
quality effect has the sign of its learning rate, not of packing.

**Step count matters independently of batch size.** Holding the padded batch
fixed and changing only the number of optimizer steps moves the optimum 1.78x
(Alpaca) and 1.29x (Dolly). Whatever the right functional form, the shift a
practitioner sees when turning packing on is not attributable to batch size
alone, because packing changes both.

### 4.5 One cell breaks the model, and overfitting is why

Neither factorial decomposes cleanly. Read Alpaca's batch effect at 1,600 steps
rather than 350 and it is **0.83x** — the optimum moves the wrong way — and
Dolly's at 430 steps is **0.64x**. Both anomalies live in the same place: the
packed cell at the long step count, which runs 4.50 epochs on Alpaca and 2.92 on
Dolly.

Those cells overfit. Alpaca's best validation loss arrives at step 684–1368
rather than at the end, and it arrives earlier the higher the learning rate, so
the apparent optimum is set by which rate overfits least by the final step.
Scored on best-during-run instead of endpoint, Alpaca's packed 1,600 optimum
rises from 2.25e-5 to 3.61e-5 — both seed 1337, so that the two metrics are
compared on one curve — and the anomaly shrinks without disappearing.

The general form is worth stating for anyone running this design: **at a matched
step count, an arm that runs many epochs reports an optimum depressed by
overfitting, and a factorial that ignores this attributes the depression to
whatever else that arm varied.** `scripts/analyze_lr_scaling.py` excludes cells
past 1.5 epochs from its headline for this reason.

### 4.6 Status of the evidence

Alpaca's four optima were re-run at seeds 1338 and 1339 on the three learning
rates bracketing each. Solving each seed's curve separately, as 3.1 requires,
the optima are 5.00e-5, 2.81e-5, 1.37e-4 and 2.35e-5, with max/min spreads
across seeds of 1.13x, 1.17x, 1.11x and 1.08x. The first three aggregate all
three seeds. The packed 1,600-step cell aggregates two: at seed 1339 its minimum
falls on the edge of the three points that were run for replication, so that
curve brackets nothing and we drop it rather than extrapolate from it. That cell
is excluded from the headline in any case (4.5). None of the effects in 4.3 or
4.4 changes sign or materially in magnitude against the single-seed values.

Dolly's three cells that are not overfitting-contaminated were replicated the
same way, with spreads of 1.08x, 1.11x and 1.03x. That spread is the yardstick
4.3 is measured against: it is what makes a 1.28x miss a failed prediction
rather than a noisy one.

The loss tables in 4.2 and the inherit-versus-retune table in 4.4 are seed 1337
throughout. They have to be: a seed changes which examples are held out, so
losses are comparable down a column within one seed and not across seeds. Only
the optima are aggregated across seeds, and only after each seed is solved on
its own curve. One consequence is worth stating plainly: the replication seeds
were run on the points bracketing each optimum, which does not include the
inherited rate, so 4.4's comparison has no cross-seed check behind it. That is
the reason 4.4 leans on the inherit-to-retune distance rather than on the sign
of the inherit-to-padding difference.

Everything here is one model size (124M) on two corpora at one window length.

## 5. Threats to validity

**One model, two corpora, two packing ratios.** Every number comes from a 124M
model at packing factors of 4.47x and 2.92x in supervised tokens per step. Two
points do not make a curve, and 4.3 is the direct evidence for that: the
exponent fitted on the first does not predict the second. Nothing here
constrains behaviour at a third ratio, a larger model, or a longer window, and
the corpus-dependence we report is a negative result about extrapolation rather
than a law estimated from two samples.

**Small absolute batch.** At 1,888 and 8,444 supervised tokens per step on
Alpaca, and 2,272 and 6,632 on Dolly, both arms are far below the batch sizes at
which large-scale scaling rules are usually measured, and Li et al. (2024)
predict this is the regime where the square-root rule holds. Results here should
not be extrapolated to production batch sizes without further points.

**fp16 dynamic range.** All runs use fp16 autocast with a gradient scaler. At
the top of the learning-rate grid, divergence from overflow and divergence from
too large a step are not distinguished by the validation loss alone.

**Packed cells run more epochs at matched step counts.** In the packed
1,600-step cell the model sees 4.50 epochs of Alpaca, and this project has
previously measured that Alpaca overfits within a single pass. Where the
optimum in that cell is set by overfitting rather than by step size, the
validation curves in §4 show it directly.

**The fixed-step batch comparison also varies data seen.** Holding the step
count fixed while changing the batch size necessarily changes how much of the
corpus is consumed, and the size of that residual confound scales with the
packing factor — which differs between our two corpora. Some unknown part of
the exponent gap in 4.3 is therefore a property of the design rather than of
the models, and we say so there rather than attributing the whole gap to the
corpora.

**The inherit comparison is one seed, and its sign turns on one grid point.**
4.4's losses are seed 1337 throughout, for the reason given in 3.1, and on
Alpaca the inherited rate beats padding by 0.004 nats while the grid point below
it loses to padding by 0.017. We report the Alpaca case as a wash for that
reason. The quantity that is robust on both corpora is the distance between the
inherited and the retuned rate, which is many times the seed spread.

**Held-out set drawn from the training distribution.** Each corpus's held-out
split shares a distribution with its own training split, and this project has
repeatedly measured that such a split ranks checkpoints differently from a
downstream one. The learning-rate optimum reported here is the optimum *for
held-out validation loss on the same corpus*, which is the right target for a
scaling question and is not the same as the optimum for a downstream
pipeline.

## 6. What to do about it

The result is not that packing is bad. Packed and padded runs are equivalent
per example by construction, packing buys 4.40x the supervised tokens per
second here at 1.02x the per-step cost, and at its own learning rate the packed
run is the best run on both corpora. The result is that the learning rate is
not part of what packing leaves alone, and that the standard advice to inherit
it gives back the quality the throughput was bought with.

**Retune, and bracket rather than scale.** Our two corpora put the packed
optimum at a matched data budget 4.86x and 2.07x above the padded one, against
packing factors of 4.47x and 2.92x. Expressed against the packing factor `p`,
that is `p^1.06` and `p^0.68` — which is exactly why we do not offer a formula.
What both corpora do fit is a bracket. Taking the padded optimum `lr_pad`, both
packed optima lie inside

    [ lr_pad * sqrt(p),  lr_pad * 1.2p ]

a range of 1.2*sqrt(p), or about 2.5x at our packing factors, which three runs
at 1.6x spacing cover. No single point inside it is right on both corpora:
scaling linearly misses by 1.09x on Alpaca and 1.41x on Dolly, and the middle
of the bracket misses by 1.44x and 1.18x — comparable, and in opposite
directions. That is the argument for running the three points rather than
trusting any one of them, and neither candidate is the real error term: what
both of them beat is inheriting, which is off by 4.86x and 2.07x. If the
minimum lands on an edge, extend and re-run; an unbracketed minimum is not a
measurement (3.3).

**Do not tune this at a matched step count.** The recipe above is for a matched
data budget, which is what a practitioner turning packing on actually has. At a
matched step count the packed arm consumes `p` times the data and, on corpora
this size, runs enough epochs that the apparent optimum is set by which rate
overfits least (4.5). That cell answers a different question and should not be
used to pick a production learning rate.

**Report which comparison you ran.** The reason the literature can assert that
the learning rate need not move, and the reason our own earlier reading of
these corpora (4.1) called the shift linear batch scaling, is the same: at a
fixed data
budget packing moves batch size and step count together, and a single ratio
measured across that diagonal is consistent with rules that disagree everywhere
else. Any claim about learning rate under packing needs to say whether the step
count was held fixed, and any factorial that holds it fixed needs to say how
many epochs each arm ran.

## 7. Reproducibility

Every number in this paper comes from `results/lr_scaling_sweep.csv`, which is
one row per run — dataset, cell, learning rate, seed, final and best validation
loss, the step the best arrived at, and wall time. The sweep is resumable and
skips rows already present, so the file is both the output and the ledger.

```
# the two factorials (28 runs each, seed 1337)
python scripts/sweep_lr_packing.py --gpus 0 1
python scripts/sweep_lr_packing.py --dataset dolly --gpus 0 1

# seed replication, on the three learning rates bracketing each optimum
python scripts/sweep_lr_packing.py --seeds 1338 1339 --lrs <three points>

# packing factors in supervised tokens per step, measured not assumed
python scripts/benchmark_packing.py --data data/sft/alpaca.jsonl
python scripts/benchmark_packing.py --data data/sft/dolly.jsonl

# the tables in 4.2, the optima, and the headline ratios in 4.3
python scripts/analyze_lr_scaling.py --dataset alpaca
python scripts/analyze_lr_scaling.py --dataset dolly

# the figure in 4.2
python scripts/plot_results.py --only lr-scaling
```

`scripts/analyze_lr_scaling.py` is the single place the optima are solved: it
fits the parabola of 3.3, keeps each seed's curve separate for the reason in
3.1, drops a seed whose curve brackets no minimum, and excludes cells past 1.5
epochs from the headline for the reason in 4.5. The tables in 4.2 and the
ratios in 4.3 are its output rather than transcriptions of it.

Hardware is a single RTX 2080 Ti per run, and the whole grid is about 17
GPU-hours (measured across the 96 runs that recorded wall time; two rows were
harvested from earlier runs of the same configs and carry none). One caveat for anyone reproducing the schedule estimate rather than the science:
the second card in this machine thermally throttles under sustained load and
takes about 2.5x as long per step, which the sweep's planner accounts for and a
naive divide-by-GPU-count does not.

## References

- Goyal, Dollár, Girshick, Noordhuis, Wesolowski, Kyrola, Tulloch, Jia, He (2017). *Accurate, Large Minibatch SGD: Training ImageNet in 1 Hour.* arXiv:1706.02677
- Krell, Kosec, Perez, Fitzgibbon (2021). *Efficient Sequence Packing without Cross-contamination: Accelerating Large Language Models without Impacting Performance.* arXiv:2107.02027
- Li, Zhao, Zhang, Sun, Wu, Jiao, Wang, Liu, Fang, Xue, Tao, Cui, Wang (2024). *Surge Phenomenon in Optimal Learning Rate and Batch Size Scaling.* NeurIPS 2024. arXiv:2405.14578
- Malladi, Lyu, Panigrahi, Arora (2022). *On the SDEs and Scaling Rules for Adaptive Gradient Algorithms.* arXiv:2205.10287
- Wang, Wang, Wang, Li, Hovy, Guo (2025). *Packing Analysis: Packing Is More Appropriate for Large Models or Datasets in Supervised Fine-tuning.* Findings of ACL 2025, 2025.findings-acl.256. arXiv:2410.08081

*(Author lists, titles and venues checked against the arXiv and ACL Anthology
records. The three claims attributed to Krell et al. in section 2 — the
advice against learning-rate scaling, the LAMB heuristic, and reducing the
computational batch size by the packing factor — are their section 3.3.)*
