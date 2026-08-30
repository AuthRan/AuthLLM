# Registered prediction: where these batches actually sit relative to the noise scale

**Written 2026-08-30, before `scripts/measure_noise_scale.py` had ever been run.**
The script was committed in c0fc88f and has produced no output in this
repository: there is no results file from it, nothing in the paper cites it, and
`git log` shows it added and never touched again. Every statement the paper makes
about the gradient noise scale is therefore an assertion.

**Drafted by the assistant and not reviewed by the author before registration.**
Strike it if you disagree; that costs nothing.

This is the eighth registered prediction in this project.

## The three places the paper asserts this

1. **Section 2.** "Our batch sizes are small in absolute terms -- 1,888 and 8,444
   supervised tokens per step -- which places us in the regime where Li et al.
   predict square-root behaviour, and makes any departure from it worth
   reporting rather than assuming." That last clause is the paper's own standard
   and this measurement is what meets it.
2. **Section 4.6.** "Li et al. (2024) place the dependence on where the batch
   sits relative to the gradient noise scale, which our batch sizes do not move
   enough to test."
3. **Section 5.** "At 1,888 and 8,444 supervised tokens per step on Alpaca [...]
   both arms are far below the batch sizes at which large-scale scaling rules
   are usually measured, and Li et al. (2024) predict this is the regime where
   the square-root rule holds."

All three say *where* the batches sit without ever having measured it. Li et al.
put the optimal learning rate's turning point at `B_noise`; the paper's exponents
run 0.385 to 1.695, spanning both sides of the 0.5 that square-root scaling
implies, and the paper offers no mechanism for that. This measures the quantity
the cited theory is about.

## What is computed

`B_simple = tr(S) / |G|^2` (McCandlish et al., 2018), estimated by fitting
`E|G_T|^2 = |G|^2 + tr(S)/T` across several token counts. It comes out in
supervised tokens per step, the same unit the paper measures batches in, so it
compares directly against 1,888 padded and 8,444 packed.

## Validity check, which comes first

`alpaca`, `alpaca_third` and `alpaca_ninth` are a corpus and two **random**
subsets of it. They share an example distribution exactly; they differ only in
how many examples exist. `B_simple` is a ratio of two expectations over that
distribution, so it should not depend on the subset at all.

**If those three do not agree within 1.5x, the estimator is too noisy here to
interpret and everything below is void.** Recorded first so that a disagreement
cannot be reinterpreted as a finding afterwards. The fit's R^2 is reported with
every value for the same reason.

## The predictions of record

**P1 -- the regime the paper claims is the regime it is in.** `B_simple` for
Alpaca at 124M exceeds **8,444**, the packed arm's supervised tokens per step.

*Falsified* if it comes in below 8,444, which would mean the packed arm sits at
or above the noise scale and sections 2 and 5 are wrong about the regime. That
would matter: Li et al.'s optimum is non-monotone with its peak at `B_noise`, so
a packed arm sitting near the peak while the padded arm sits well below it is a
candidate mechanism for a shift larger than square-root — and the paper reports
exponents up to 1.695, which no square-root regime explains.

**P2 -- the noise scale does not explain section 4.6.** Given the validity check
above passes, `B_simple` is the same across `alpaca`, `alpaca_third` and
`alpaca_ninth` while their exponents are **0.385, 0.670 and 1.055** — a spread of
2.7x. A quantity that does not move cannot explain one that moves by 2.7x.

*Falsified* if the three disagree by more than 1.5x **and** in the same order as
the exponents, which would mean the scale dependence is a noise-scale effect
after all and section 4.6's central result has a mechanism it does not currently
claim. That is the outcome I would most like to be wrong about, and it is why
P2 is of record rather than being left as a remark.

**Point estimates.** I expect `B_simple` in the tens of thousands of supervised
tokens — comfortably above 8,444, so P1 holds — and the three subsets within a
few per cent of each other. I hold P1 more loosely than P2: instruction-tuning
gradients on a small model may be far noisier than the pretraining setups
`B_noise` is usually quoted for, and a value near 10,000 would not surprise me.

## Exploratory, explicitly not of record

*Model size.* The same measurement at 30M and 7M, where the exponents are 1.300
and 1.695 against 124M's 1.055. Here the model changes, so `B_simple` genuinely
can differ, and if it moves in the direction Li et al. would need it to, that is
a candidate mechanism for section 4.7 rather than for section 4.6. This is not
of record because I have no principled expectation for the direction and would
be fitting a story to whatever came out. It is reported either way.

*Packed against padded.* The script's `--packed` flag measures the packed
dataset, which differs from the padded one in which examples share a batch and
not in the objective. Any difference is informative about the estimator, not
about the paper's claims.

## Limitations recorded in advance

1. **One checkpoint.** `B_noise` is a property of a model at a point in
   training, and the fine-tune moves the weights. This measures it at the base
   model, which is where the fine-tune starts and not where it ends.
2. **Token-mean, not per-sequence.** The estimator assumes the loss is a
   token-mean over supervised tokens, which is what section 3.1 says it is.
3. **A ratio of two fitted quantities.** The intercept is the smaller and the
   worse-conditioned of the two; if it comes out non-positive the script says so
   and the measurement fails rather than returning a number.
4. **Li et al.'s `B_noise` is not exactly McCandlish's `B_simple`.** They are the
   same idea and not the same estimator, so a value near the boundary should not
   be read as decisive either way.

## How it will be scored

```
python scripts/measure_noise_scale.py --model configs/model/medium.yaml \
    --init-from checkpoints/medium/step_20000.pt \
    --data data/sft/<corpus>.jsonl [--packed] --repeats <n>
```

for `alpaca`, `alpaca_third`, `alpaca_ninth` at 124M, then the two smaller
models, with the results written to `results/noise-scale.md`. The validity check
and P1 and P2 are read off that table. No discretion.

## Addendum, written the same day and before the scoring run

`scripts/measure_noise_scale.py` had never been executed, so before depending on
it I smoke-tested it on a pair that is **not** in the set above: the 7M model on
Dolly, `--sizes 1 2 4 --repeats 2`, on CPU. It ran and printed a number.

Recording this because I have now seen an output of the estimator, and a
registration that hides that is worth less than one that admits it:

* the smoke test returned `B_simple = 2,137` supervised tokens, well **below**
  the 8,444 that P1 predicts the real settings will exceed;
* it is a two-repeat estimate, and its own `mean tokens` column ran 97.0, 47.5,
  268.5 across sizes 1, 2 and 4 — not monotone in the number of rows, which it
  must be in expectation. That is sampling noise, not a measurement;
* its R^2 of 0.9999 is not reassuring either: three points almost always fit a
  line.

**The predictions above are not revised.** P1 stands as written. If it fails,
this addendum is the record that I had seen a hint it might and did not quietly
move the goalposts. What the smoke test does change is the method: the scoring
run uses more repeats than the default and reports the `mean tokens` column, so
that a non-monotone one is visible as the failure it is rather than being
averaged into a plausible-looking ratio.

## Second addendum: the first scoring run failed, and why

**Written before the second run, with no `B_simple` yet measured for any
registered setting.**

The scoring run was made at 32 repeats and returned **"fit failed" for all five
settings**: the fitted intercept `|G|^2` was negative everywhere, so the ratio
`tr(S)/|G|^2` does not exist. That is limitation 3 above arriving exactly as
written, and under the registration it means the measurement failed rather than
that anything was learned. P1 and P2 are unscored.

The cause is visible in the data and is not subtle. `measure_noise_scale.py` ran
one backward pass per batch, so its largest batch was 8 sequences carrying **443
supervised tokens** — against the 1,888 and 8,444 the paper's steps carry. Every
point sat in the regime where the noise term `tr(S)/T` dominates completely, so
extrapolating to `1/T = 0` ran off the bottom:

```
  rows  mean tokens    mean |G|^2
     1         41.8  7.584403e+02
     2        101.1  2.212723e+02
     4        212.1  9.619484e+01
     6        380.2  3.792629e+01
     8        443.4  3.687135e+01
  |G|^2 (intercept) = -6.12e+01   R^2 = 0.9909
```

An R^2 of 0.99 on a line whose intercept is negative is the estimator saying it
can see the slope and cannot see the intercept.

**What changed, and what did not.** The estimator now accumulates gradients over
micro-batches, weighted by token count, so the batch it can measure is bounded by
patience rather than by memory. That is a fix to reach the range the paper is
about; it is not a change to what is being estimated, and it was validated by
checking that one backward pass over 8 sequences and four micro-batches of 2 give
the same squared norm to five significant figures (6.003064e+01 against
6.003014e+01). The second run sweeps to 256 sequences, which is roughly 10,700
supervised tokens and past the packed arm's 8,444, so the fit interpolates across
the paper's batch sizes instead of extrapolating from an order of magnitude below
them.

**P1 and P2 are unchanged and unrevised.** The failure was in reaching the
measurement, not in what it would say, and no value for any registered setting
has been seen. If the second run also fails, that is the answer: this estimator
cannot resolve the noise scale on this hardware, and the paper's three
assertions about the regime stay assertions — which would itself be worth
reporting, because the paper currently states them as though they were settled.
