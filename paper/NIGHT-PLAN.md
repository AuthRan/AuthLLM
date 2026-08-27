# Status — packing learning-rate paper

*Written so the state survives a context reset. Update it when something lands.*

## In flight (2026-08-28, overnight)

Two seed replications, chained: the quality series (12 runs, `lr_scaling_quality`)
and then the 7M matched-budget ledger (24 runs, `lr_scaling_mini2k`), the second
launched behind `scripts/wait_for_gpus.py` when the first exits. Both are
resumable, so a re-invocation of `replicate_series_seeds.py` with the same
arguments picks up whatever is missing.

When they land: `export_exponents.py`, then `update_paper_counts.py`, then the
write-ups that are still owed --

- **section 4.7.2**, the size-versus-quality control, scoring the fifth
  registered prediction. At seed 1337 it reads 0.916 against 1.055 at 124M and
  1.695 at 7M, which is H1, but one seed carries no bound.
- **section 4.7.1**, extended with the 7M pair so the budget axis is two model
  sizes rather than one. Appendix D already lists `lr_scaling_mini2k.csv`
  against 4.7.1 on that assumption.
- **Appendix C**, the seed accounting for both, and **Appendix D**, the seventh
  ledger row.
- The abstract, contribution 7, the 4.7 confound paragraph, section 5's threat
  and section 7 all currently say the quality confound is registered and
  unresolved.

Also done tonight: everything above was uncommitted (769 files, and the second
half of the paper); it is committed now. `paper/build_tex.py` renders the arXiv
package into `paper/arxiv/`, guarded by `tests/unit/test_build_tex.py` -- it has
never been compiled, this machine has no TeX. `wait_for_gpus.py` now exists,
having been recorded below as a fix that had been made. `update_paper_counts.py`
was missing the quality ledger that `export_exponents.py` had.

## Where it stands (2026-08-26)

**Paper**: `paper/paper.md`, ~12,900 words, published at
<https://claude.ai/code/artifact/a62b717b-28f8-43a8-ad66-927a75cf3bf2>.
`paper/build_page.py` renders it; `scripts/update_paper_counts.py` keeps the run
count and GPU-hours in sync with the ledgers — 551 runs, ~49 GPU-hours.

**Ledgers**, five, one per base model, all resumable and all tallied by
`update_paper_counts.py` and `export_exponents.py` (keep those two lists in
step):

| ledger | base model |
| --- | --- |
| `results/lr_scaling_sweep.csv` | 124M |
| `results/lr_scaling_small.csv` | 30M, 39.4 tok/param |
| `results/lr_scaling_mini.csv` | 7M, 39.5 tok/param |
| `results/lr_scaling_small9k.csv` | 30M, 19.7 tok/param |
| `results/lr_scaling_ckpt.csv` | 124M grid extension |

`scripts/export_exponents.py` -> `results/exponents.csv` is the single table of
every matched-budget comparison; the figures read it rather than recomputing.

## The result, in one paragraph

Packing shifts the optimal learning rate a long way, and inheriting the padded
rate throws the gain away. The shift is a *batch-size* effect and not a property
of packed representation — a padded run with accumulation raised to match a
packed step on tokens, examples and data seen finds the same optimum, at several
times the compute. How large the shift is has no single rule: expressed as an
exponent against the packing factor it runs 0.385 to 1.695 across thirteen
settings. Corpus identity is ruled out, the packing ratio is unsupported once
seed spread is carried through, and what moves it is the scale of the run —
confirmed by three predictions registered before the runs existed, on both
corpora and at all three model sizes. Model size moves it too, in a direction
all six comparisons agree on but resolved only across the full 17x span. How
well the base model was pretrained does not move it at all.

## Experiments, and what each was for

| experiment | question | verdict |
| --- | --- | --- |
| 2x2 factorial, two corpora | does lr* move under packing? | yes, 4.86x and 2.07x at one epoch |
| wide-batch control (4.4) | batch size, or packed representation? | batch size; rows rule rejected 4.6x and 3.3x |
| length terciles (4.5) | corpus, or packing ratio? | corpus ruled out; ratio unsupported |
| scale series ninth/third/whole (4.6) | is it scale? | yes, monotone over 9x, both steps clear their bounds |
| `dolly_third` (4.6) | does scale replicate? | yes, 0.456 against 0.681, 1.7x the bound |
| decomposition cells (4.6) | batch term or step term? | both fall with scale; the step term faster |
| 30M and 7M models (4.7) | does any of it hold at another size? | shift grows as the model shrinks: 4.86x, 7.01x, 12.66x; scale dependence holds at all three |
| 30M @ step 9,000 (4.7.1) | is the size effect a pretraining-budget effect? | no — halving the budget moves the exponent 0.002 and 0.019 |
| metric bias (Appendix B) | does endpoint scoring bias the optimum? | only past ~3 epochs; 15 cells under 1 epoch unmoved |

## Registered predictions

All written before the deciding runs existed. `results/registered-prediction-scale.md`
holds the first two and the retraction below; `results/registered-prediction-model-size.md`
holds the third, and now carries its scoring.

1. `alpaca_third` should land near 0.67 if scale governs. Measured 0.670.
2. `dolly_third` should land below Dolly's 0.681 by more than the seed bound.
   Measured 0.456, a gap of 0.225 against a bound of 0.135.
3. At 30M, `alpaca` should exceed `alpaca_third` by more than 0.135, or §4.6's
   scale reading is 124M-specific. Measured gap 0.531, 3.9x the margin. The
   seed-pass limitation that prediction recorded in advance was then run: all
   six 30M cells bracket at three seeds, none dropped, and it did not move the
   verdict.

A fourth, from §4.2 (Alpaca's exponent should predict Dolly's), is falsified and
reported as such.

## Things that were findings and then were not

Kept deliberately — the paper reports them as retractions.

- **A packing-ratio trend.** The three length terciles spanned 0.151 of exponent
  in tidy monotone order. Seed replication put the spread at 0.7x its own noise.
- **Linearity in log(scale).** The three scale points sat exactly 3x apart with
  exponent differences of +0.369 and +0.371, residuals ±0.0006. Flagged at the
  time as coincidence, because the residuals were two orders of magnitude below
  the seed bounds; replication moved them to +0.285 and +0.385.
- **The bracket in section 6.** `[lr_pad * sqrt(p), lr_pad * 1.2p]` held on the
  five settings it was drawn from and now fails on five of the thirteen measured
  — below the floor at the three smallest scales, above the ceiling at 30M and
  7M, the latter by a factor of 2.4. Replaced by a range, `p^0.4` to `p^1.7`,
  which is a real sweep (3 to 6 points at 1.6x spacing) and is stated as such.
  Note it widened again when 7M landed: each new setting has so far stretched
  it, never narrowed it.
- **A ±0.006 bound on the 7M random third.** Two of its three replication seeds
  put their minimum on the top edge of the replicated window and were dropped by
  the usual rule, leaving one curve and a bound that reflected nothing. The
  drops were biased upward, so dropping them would have biased the exponent
  down. Fixed by extending the window (4 runs); the bound went to ±0.174.

## Bugs found and fixed

- **Log-path collision.** `run_id` is (dataset, cell, lr, seed) and says nothing
  about the base model, so the 30M sweep appended its curves to the 124M runs'
  log files: 29 polluted, and the 30M `best_val_loss` picked up 124M minima.
  Logs repaired by truncating at the step reset and validated against the values
  the ledger recorded at run time (29/29 match); the 30M ledger was discarded
  and re-run. Fixed by namespacing logs, configs and checkpoints per ledger.
- **Unsorted points to `interpolated_optimum`.** It takes neighbours by index,
  so unsorted input silently returns a wrong optimum — 1.16x where the answer
  was 1.60x. Caught only by cross-checking against the existing script.
- **An OOM cascade that burned a whole grid in 0.9 min.** Starting a sweep
  before the previous one had released GPU memory OOMed its first run; the
  sweep marks a failed run FAILED and *immediately starts the next one*, so a
  single busy card turned into 15 consecutive failures and an empty ledger.
  Worse, the first two runs survived and kept training as orphans holding 8.6 GB
  each, after the sweep had already given up on them and exited — so the cards
  stayed blocked with nothing recording the results. A 124M fine-tune needs
  ~8.6 GB of an 11.26 GB card and GPU 0 carries ~0.9 GB of desktop, so the
  margin is about 1.7 GB and there is none to spare for a lingering process.
  Fixed in two places: sweeps are launched behind a `wait_for_gpus` preflight
  that blocks until both cards are under 1.5 GB, and `sweep_lr_packing.py` now
  takes `--max-consecutive-failures` (default 3) and stops the grid with an
  explanation instead of sprinting through it. Still open: the sweep does not
  reap its children on exit, which is how two orphans kept training after it
  had given up on them.
- **Watchers that match themselves.** A `pgrep -f` pattern appearing in the
  watcher's own command line deadlocked the queue twice with both GPUs idle, and
  a `pkill -f` killed the shell that issued it. Every watcher now polls row
  counts in the results file instead.

## What is left

- **Venue.** An efficiency or post-training workshop is the honest target;
  ENLSP and OPT at NeurIPS fit the scaling framing, *Insights from Negative
  Results in NLP* fits the falsifications. Deadlines not checked.
- **Length.** ~12,900 words is now well over any of those workshops' limits and
  is the most pressing problem. A cut would move §4.5, the estimator-robustness
  material and §4.7.1 to an appendix, and compress §4.7's six bolded claims to
  the two that clear their bounds.
- **Author list and affiliation.**
- **Cheapest open check.** `checkpoints/mini/step_2000.pt` (18.0 tok/param) is
  unswept; sweeping it would put the 7M model on the budget axis too. §4.7.1
  currently establishes that axis at 30M only, and the 7M row is assumed rather
  than measured. 7M runs are 0.4–3 min each.

## Standing constraints

- Browser work: this PC's Chrome only, and no local Chrome is connected.
- Commits on this repo carry no Claude attribution.
- GPU 1 thermally throttles to ~300MHz under load, ~2.5x slower than GPU 0;
  `scripts/sweep_lr_packing.py` accounts for it in its schedule estimate.
