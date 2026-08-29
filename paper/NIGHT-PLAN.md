# Status — packing learning-rate paper

*Written so the state survives a context reset. Update it when something lands.*

## What landed overnight, 2026-08-28

**The quality control (§4.7.2) and the 7M budget control (§4.7.1) are measured,
seed-replicated and written up.** 42 runs across two ledgers plus 6 more to
extend two windows. Every cell in every ledger now brackets at three seeds
except the two 124M curves Appendix C has always recorded as dropped.

- **§4.7.2, the fifth registered prediction: H1 confirmed.** A 124M model taken
  back to `step_500`, at perplexity 107.0 against the 7M model's 115.2, reads
  **0.906 ± 0.204** — 0.789 below 7M's 1.695 (2.7x the registered margin of
  0.291) and 0.149 from 124M's own 1.055 (0.6x the combined bound). Quality
  moved by 4.6x in perplexity and the exponent stayed with the parameter count.
  §4.7's trend is about model size.
- **§4.7.1 now covers two model sizes.** The 7M pair reproduces the 30M null:
  exponent differences of 0.019 and 0.103 against bounds of 0.266 and 0.424,
  while both optima rise together (1.44x/1.40x and 1.84x/1.57x). Four pairs, two
  sizes, same answer.
- **Three curves needed the window extended, and one of them mattered.**
  §4.7.2's padded cell had a seed sitting above its window; dropping it under
  the usual rule would have pushed the exponent *up*, toward the hypothesis the
  registration argued against (0.946 on two seeds against 0.906 on three).
  Extended instead, as was done for the 7M random third. Recorded in Appendix C
  and in the prediction's scoring.

**Repo work.** Everything was uncommitted — 769 files and the second half of the
paper — and is now in ten commits. `paper/build_tex.py` renders the arXiv
package into `paper/arxiv/` (main.tex, figures, abstract.txt, README), guarded
by `tests/unit/test_build_tex.py`, which stands in for the compiler this machine
does not have: brace and environment balance, table columns, unescaped
specials, surviving markdown, and a simulation of LaTeX's section counters so
that §4.7.1 and Appendix C still come out numbered as the prose cites them.
`scripts/wait_for_gpus.py` now exists, having been recorded below as a fix that
had been made. `update_paper_counts.py` was missing the quality ledger that
`export_exponents.py` had, so the paper was under-reporting its own compute.

## Where it stands (2026-08-26)

**Paper**: `paper/paper.md`, ~12,900 words, published at
<https://claude.ai/code/artifact/a62b717b-28f8-43a8-ad66-927a75cf3bf2>.
`paper/build_page.py` renders it; `scripts/update_paper_counts.py` keeps the run
count and GPU-hours in sync with the ledgers — 649 runs, ~63 GPU-hours.

**Ledgers**, seven, one per base model, all resumable and all tallied by
`update_paper_counts.py` and `export_exponents.py` (keep those two lists in
step):

| ledger | base model |
| --- | --- |
| `results/lr_scaling_sweep.csv` | 124M |
| `results/lr_scaling_small.csv` | 30M, 39.4 tok/param |
| `results/lr_scaling_mini.csv` | 7M, 39.5 tok/param |
| `results/lr_scaling_small9k.csv` | 30M, 19.7 tok/param |
| `results/lr_scaling_mini2k.csv` | 7M, 18.0 tok/param |
| `results/lr_scaling_quality.csv` | 124M at perplexity 107.0 |
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

4. At 124M and the 7M model's perplexity, the exponent should stay near 1.055
   and land more than 0.291 below 1.695, or §4.7 is measuring base-model quality
   rather than model size. Measured 0.906: 0.789 below, 2.7x the margin.
   `results/registered-prediction-size-vs-quality.md` carries its scoring.

A fifth, from §4.2 (Alpaca's exponent should predict Dolly's), is falsified and
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

*Rewritten 2026-08-29. Three of these were open only because the machine was
believed to have no network. It has one.*

### Deadlines, now checked

**Pre-to-Post — Transitioning from Pre-Training to Post-Training**, NeurIPS
2026, <https://pretrain2posttrain.github.io/>, is the best fit this project has
found, and it is the target unless the author disagrees.

| | |
| --- | --- |
| deadline | **4 September 2026, 11:59pm AoE** (extended from 29 August) |
| notification | 29 September 2026 |
| archival? | **non-archival** — arXiv is unaffected, and posting first is fine |
| length | short 4–5 pages, or long at the NeurIPS main-conference limit |
| **references and appendices do not count toward either limit** | |

Its call lists *interactions between pre-training and post-training* as a topic.
That is §4.7.1 and §4.7.2 exactly: what a base model's pretraining budget, and
its quality, do to the fine-tuning learning rate. The paper was not written for
this workshop and reads as though it was.

Also on 4 September, and a real second option: **OPT 2026**, <https://opt-ml.org/>,
deadline 4 September AoE, notification 29 September, this year themed *Can
Anything Beat Adam? Frontier Optimizers*. The batch-size framing fits; the theme
is about optimizers rather than their hyperparameters, so it is the weaker fit
of the two.

Two others were checked and are out. **ENLSP does not exist at NeurIPS 2026** —
it ran 2022 through 2024 and is not in this year's accepted list, so the plan's
first-named venue is gone. **Insights from Negative Results in NLP** is alive and
co-located with EMNLP in Budapest, 22–29 October 2026, but its site publishes no
call yet; worth a second look, since the retractions in this paper are exactly
its subject.

Two deadlines were missed by hours while this was being checked: **AXIOM
(Foundations of Efficient Deep Learning)** and **LIGHT (Deployable Small
Foundation Models)**, both 29 August. LIGHT in particular wanted precisely this
paper's finding — that the cost of inheriting the rate grows as the model
shrinks. That is the price of not checking dates for three days.

### Length — measured 2026-08-30, and four times worse than this file claimed

The entry that stood here called the cut "cheap" and said "nothing needs to
leave the paper". **Both were wrong, and not by a little.** They were written
without compiling and counting. Now counted, from the 29-page PDF:

**§1–§7 is 21 pages.** References are page 22, appendices 23–29. The limits,
re-checked against the call on 2026-08-30 (deadline confirmed 4 September
11:59pm AoE, notification 29 September, non-archival, references and appendices
excluded from both tracks):

| track | limit | cut needed from 21 |
| --- | --- | ---: |
| short | 4–5 pages | **−16** |
| long | 9 pages (NeurIPS 2026 main-track limit, confirmed) | **−12** |

Measured cost of each section, so tomorrow's cut is arithmetic:

| section | pages | | section | pages |
| --- | ---: | --- | --- | ---: |
| §1 Introduction | 1.70 | | §4.4 batch or packing | 1.64 |
| §2 Related work | 1.14 | | §4.5 not a function of | 0.91 |
| §3.1 Setup | 0.95 | | §4.6 + §4.7 | 3.79 |
| §3.2 Confound and design | 0.59 | | §4.7.1 pretraining budget | 0.91 |
| §3.3 Grid | 0.58 | | §4.7.2 size or quality | 0.94 |
| §3.4 Wide-batch control | 0.29 | | §5 Threats | 1.88 |
| §4.1 Full factorial | 0.39 | | §6 What to do | 1.77 |
| §4.2 Second corpus | 1.43 | | §7 Conclusion | 0.56 |
| §4.3 What does replicate | 0.82 | | | |

The cut this file used to name — §4.5, the estimator-robustness material and
§4.7.1 to appendices — is **2.7 pages against a 12-page gap.** It is not the
job. The job is to rewrite §1–§7 as a 9-page paper that cites the arXiv version
for everything else, and that is a day of writing, not an afternoon of moving
blocks. §4.6+§4.7 at 3.79 pages and §5+§6 at 3.65 are where the pages actually
are, and neither was on the old list.

**Recommendation: the long track.** Nine pages can carry the factorial, the
batch-size control, the scale result, the model-size result and the retractions.
Four to five cannot; at that size this becomes an extended abstract and the
retractions — which are the paper's most distinctive content — are the first
thing to go. The author should confirm, because it is a positioning decision
and not a mechanical one.

The arXiv version keeps its full length; arXiv has no limit.

### Anonymity — an open question that changes the title page

NeurIPS 2026's main track is double-blind and its handbook forbids identifying
information. **The workshop's own call does not say** whether it inherits that,
and OpenReview renders through JavaScript so the group page could not be read
from here. This has to be settled before submitting, but it is
cheap either way: `paper/paper.md` was grepped on 2026-08-30 for the repository,
the published web version, the author's name and the address, and **carries none
of them**. All four live only in `build_tex.py`'s `AUTHOR`/`AFFILIATION`/
`CONTACT`, so a blind build is a flag that suppresses one title block, not a
pass over the prose. The arXiv build keeps all of it either way.

### Still open

- **Author list and affiliation.** `paper/build_tex.py` carries `AUTHOR` and
  `AFFILIATION` near the top and there is a `% TODO` beside them in the
  generated preamble. They currently read "Ashutosh Ranjan / Independent
  Researcher", with no contact address. This is the one thing in the arXiv
  package not derived from the markdown, and the only item on this list that
  needs the author rather than the machine.
- **Endorsement.** A first `cs.LG` submission needs one, which is an
  account-level step and cannot be done from here.
- **Cheapest open check — running now.** `checkpoints/medium/step_2500.pt`
  (perplexity 39.4, against the 30M model's 38.0), the middle point of §4.7.2's
  quality series. Ledger `results/lr_scaling_quality2500.csv`, prediction
  registered in `results/registered-prediction-quality-midpoint.md` before the
  answer existed (with the one row that had already landed quoted in it). When
  it finishes: register the ledger in `export_exponents.py` **and** in
  `update_paper_counts.py`, replicate the bracketing window to seeds 1338/1339,
  score the prediction, then rewrite §4.7.2's closing caveat and §5's, both of
  which currently say the quality axis has two points.

### Closed since this list was written

- **Compiled.** Tectonic 0.17.0's static binary needs no TeX install and no
  sudo, only network. 29 pages, no overfull or underfull boxes, no LaTeX
  warnings, no undefined references. The command is in `paper/arxiv/README.md`.
  Compiling also found a stale paragraph in Appendix F that described a table
  row no longer in the table, and confirmed the two checklist items that were
  waiting on it: Appendix F's nine-column table sets legibly with no landscape
  page, and LaTeX's section numbers match the ones the prose cites.
- **The abstract fits arXiv's form.** 1,890 characters against the ~1,920 cap.
  `build_tex.py` now fails loudly if an edit pushes it back over.

## Standing constraints

- Browser work: this PC's Chrome only, and no local Chrome is connected.
- Commits on this repo carry no Claude attribution.
- GPU 1 thermally throttles to ~300MHz under load, ~2.5x slower than GPU 0;
  `scripts/sweep_lr_packing.py` accounts for it in its schedule estimate.
