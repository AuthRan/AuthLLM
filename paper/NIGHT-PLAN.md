# Status — packing learning-rate paper

*Written so the state survives a context reset. Update it when something lands.*

## What landed overnight, 2026-08-30

**The paper is submittable.** A workshop version cut to nine content pages lives
in `paper/workshop.md` and builds in the real NeurIPS style; `paper/paper.md`
keeps its full length for arXiv. 674 runs, ~70 GPU-hours.

### The measurement that mattered most

**Sections 1-7 were measured against the wrong ruler for three days.** The
"21 pages against a 9-page limit" figure was taken in the arXiv preamble, and
the limit is defined in `neurips_2026.sty`. In that style the same text was 9.6
pages, not 11.9: the column is an inch narrower but Times at 10pt beats Computer
Modern at 11pt by far more than the width costs. `build_tex.py --neurips` now
builds in the vendored style so this cannot recur, and `--anonymous` selects
between its `dblblindworkshop` and `sglblindworkshop` options, which is the whole
of the anonymity question.

**Compression by rewriting does not work.** Twelve passes took 21 pages to about
10 and then stopped dead -- two consecutive passes rewrote whole sections and
saved 0.08 and 0.00 pages. Dense prose rewritten by the same hand comes out the
same length. What moved the last page and a half was *deleting claims the paper
made twice*, an abstract that was 1,890 characters only because that is arXiv's
form cap, and a figure drawn 4.9in tall for thirteen rows.

### Two registered predictions scored, one measurement failed

- **The midpoint of the quality series (§4.7.2)** -- 22 runs, all six curves
  bracketing at three seeds with nothing dropped, the cleanest ledger here. Both
  registered conditions hold: **1.133 ± 0.107** against a requirement to land
  below 1.178, series spanning 0.227 against a 0.332 cap. Reported with its
  weaknesses: it passes by 0.045 where the extreme point cleared by 2.7x, the
  registered point estimate of 1.00 was wrong, and the three estimates (1.055,
  1.133, 0.906) are **not monotone**. No pair separates -- 0.46x, 0.62x, 0.98x of
  their bounds -- so the claim is flatness at this resolution, not a shape.
- **The gradient noise scale** -- registered because the paper asserts in three
  places where its batches sit relative to `B_noise` and had never run the script
  that measures it. The first scoring run **failed**: negative intercept
  everywhere, because the estimator did one backward per batch and topped out at
  443 supervised tokens against the 1,888 and 8,444 the paper uses. It now
  accumulates gradients over micro-batches, token-weighted, and a second run is
  in flight.
- **A downstream test of §4.3**, registered because §7 names a downstream metric
  as one of four things that would move the work forward and it is the only one
  this machine can do. §4.3's three conditions re-run with checkpoints kept --
  they reproduce §4.3 to **0.0000 nats** -- then scored on Dolly's held-out
  split, which none of them was fine-tuned on. In flight.

### Every curve now brackets

The last three 124M curves that had a seed on the edge of its window were
extended rather than dropped, because dropping was directional in all three: the
ninth's dropped seed sat above its window and the middle tercile's below it. **No
curve anywhere in this paper is now on fewer than three seeds, and none is
dropped.**

It moved two exponents and widened both bounds, since the dropped curve was in
each case the furthest from the other two. The middle tercile 0.686 ± 0.067 ->
**0.651 ± 0.133**; the random ninth 0.385 ± 0.100 -> **0.412 ± 0.136**. The ninth
is one end of the headline range, so that is now **0.412 to 1.695**, and §4.6's
first scale step clears its bound by 1.8x where it cleared by 2.6x. Nothing
changes sign; the scale series is measurably weaker at its small end than the
previous version of Appendix C claimed.

### Two figures added, and a bug they exposed

`09-lr-scaling-quality` puts the exponent against base-model perplexity -- the
124M series flat across the whole range while the smaller models sit far above at
the same perplexities. `10-lr-scaling-bracket` puts all thirteen settings against
the retracted bracket. Both read `results/exponents.csv`; the perplexities come
out of each model's own pretraining log rather than being typed.

Rendering the compiled PDF rather than the PNGs showed that **three of five
figures were illegible in the paper** -- drawn 11.6in wide and set in a 5.5in
column, their 8.5pt labels printed at 4pt. `style.paper_text()` scales the type
with the canvas.

### Bugs and gaps closed

- **The sweep left runs on the cards.** Worker threads are daemons, so a killed
  driver left `finetune.py` children training with nobody recording them. Now
  reaped on the way out; the test caught that the first version signalled without
  waiting, which leaves a zombie still holding its card.
- **Two sweeps could share a card.** They did, at 05:09, and a 124M run died at
  step 220 with no error in its log. `wait_for_gpus` answers "is the card free
  now" and cannot answer "will it still be free". There is a per-card lock now.
- **The batch and step terms in §4.6 were typed by hand** and had gone stale --
  0.379 against a real 0.385. `scripts/export_decomposition.py` derives them, and
  two tests guard them.
- **A citation overreached.** The paper said Wang et al.'s claim "is not
  accompanied by a sweep"; their §5.3 does vary batch size and learning rate
  together. Corrected to the narrower and defensible claim after reading both
  cited papers rather than trusting memory of them.

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
| `results/lr_scaling_quality2500.csv` | 124M at perplexity 39.4 |
| `results/lr_scaling_downstream.csv` | 124M, 4.3's three conditions with checkpoints kept |
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

### Length — done. The workshop version fits nine pages.

`paper/workshop.md` fits **nine content pages** in the NeurIPS style: the
conclusion ends at the bottom of page 9 and page 10 opens with the references.
`paper/WORKSHOP-CUT.md` records what moved and what it cost.

Two corrections to what this file said earlier, both worth keeping:

- The "21 pages against 9" figure was measured in the arXiv preamble, and the
  limit is not defined there. In the NeurIPS style the same text was 9.6 pages,
  not 11.9 — `textwidth` is an inch narrower but Times at 10pt beats Computer
  Modern at 11pt by more than the width costs. `build_tex.py --neurips` now
  builds in the real style so this cannot recur.
- Compression by rewriting stalled completely at about 10 pages: two passes in a
  row saved 0.08 and 0.00 pages. What worked was deleting claims the paper made
  twice, shortening an abstract that was sized for arXiv's submission form, and
  reshaping a figure that was drawn tall.

Nothing was dropped: appendices are excluded from the limit, so sections 1-7
keep a summary in place and the rest moved to appendices H through M. All three
main-body figures stayed.

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
