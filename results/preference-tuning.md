# What Preference Tuning Actually Changed

Every stage before this one taught the model by showing it text to copy.
Pretraining copied FineWeb-Edu; the instruction stages copied Alpaca and
Dolly. Cross-entropy carries exactly one instruction — *be more like this* —
and no training example it has ever seen contained two answers, so nothing in
the pipeline could say that one answer is better than another.

DPO can. This page is about what happened when it did, and the short version
is that **the metric the run optimizes moved ten points and the model's actual
preferences moved half a point.**

## What was run

The starting model is the best instruction-tuned checkpoint this repo has
(`checkpoints/sft_dolly_packed3e5/step_940.pt`, the one that ends
[§10.6](../README.md#106-sequence-packing--the-89-that-was-padding)'s packed
pipeline). The data is Anthropic's HH-RLHF, filtered to single-exchange pairs
— 49,241 of its 160,800 rows, the rest being multi-turn dialogues this repo's
single-instruction template cannot represent — of which 48,800 fit a 512-token
window on both sides.

Three learning rates, 400 steps each, at 32 pairs per optimizer step. Each is
a complete cosine cycle so their endpoints compare fairly, the same rule the
instruction-tuning sweeps used.

| max_lr | held-out DPO loss | best during the run | DPO accuracy | margin | chosen reward | rejected reward |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1.0e-6 | 0.6661 | 0.6661 (step 400) | 60.0% | +0.069 | -0.156 | -0.225 |
| 5.0e-6 | 0.6573 | **0.6534 (step 100)** | **61.9%** | +0.117 | -0.336 | -0.452 |
| 2.0e-5 | **0.6562** | 0.6562 (step 400) | 60.6% | +0.167 | -0.660 | -0.827 |

Read the endpoint column alone and 2.0e-5 wins. Read the whole curve and it is
the worst-behaved run in the sweep:

```
lr 1.0e-6   .6862  .6757  .6714  .6698  .6686  .6674  .6668  .6661
lr 5.0e-6   .6738  .6534  .6558  .6616  .6651  .6620  .6594  .6573
lr 2.0e-5   .6987  .6584  .6854  .6953  .6845  .6696  .6622  .6562
             50    100    150    200    250    300    350    400
```

At 2.0e-5 the held-out loss spends steps 50-250 *above* the 0.6931 it started
at — worse than not training at all — and only comes back because the cosine
schedule anneals the learning rate to near zero. The endpoint is real, but it
is the endpoint of a run that was damaging the model for most of its length.

## Ranking, on a split the run never saw

The sweep's own validation pairs are a slice of the same file the run trained
on: same prompts, same labellers. So every checkpoint was scored again on
HH-RLHF's own test split — 2,574 pairs, different conversations —
by `scripts/eval_preference.py`.

| checkpoint | raw accuracy | chosen shorter | chosen longer | per-token accuracy | DPO accuracy | margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `sft` (the model DPO started from) | 46.3% | 92.8% | 8.3% | 54.3% | 50.0% | +0.0000 |
| DPO, 1.0e-6 | 46.4% | 92.9% | 8.4% | 55.2% | 57.1% | +0.0505 |
| DPO, 5.0e-6 | 46.6% | 93.0% | 8.6% | 56.1% | 58.4% | +0.1083 |
| DPO, 2.0e-5 | **46.8%** | 92.7% | 9.1% | **56.6%** | **59.9%** | +0.1774 |

**DPO accuracy** is the number the run optimizes: does the policy prefer the
chosen answer *more than the frozen reference did*. It goes from 50% (where it
starts by construction — the policy is the reference) to 59.9%, on pairs from
a different split. The objective is being learned, and it generalizes.

**Raw accuracy** asks the model alone, with no reference in it: is
`log pi(chosen) > log pi(rejected)`? It goes from 46.3% to 46.8%. Half a
point, across a twentyfold range of learning rate.

Those two numbers are not in conflict. DPO optimizes a *difference from where
you started*, and that difference is exactly what improved. It never promised
that the model would end up preferring good answers in absolute terms, and it
did not.

## The length column is the whole story

The two middle columns split raw accuracy by which answer is longer, and they
are the most informative thing on this page:

- when the chosen answer is **shorter** than the rejected one, the model ranks
  it first **92.8%** of the time
- when the chosen answer is **longer**, **8.3%** of the time

That is not a preference model. That is a length detector. Summed
log-probabilities are all negative, so every extra token costs about 2-3 more
nats, and an answer eight tokens longer starts ~20 nats behind — far more than
any difference in content quality. HH-RLHF's chosen answers average 80 tokens
against the rejected answers' 73, so the shortcut is *available* and the base
model takes it every time.

And DPO barely touched it: 92.8% → 92.7% and 8.3% → 9.1%. Per-token accuracy,
which divides the shortcut out, is where the real movement is — 54.3% → 56.6%,
a genuine but small 2.3-point gain in ranking by content.

I would not have seen any of this from the training curve. The loss falls, the
accuracy climbs, and both are true statements about a quantity that is not what
anyone means by "the model got better".

## Both rewards go down

The other thing worth staring at is the sign of the implicit rewards in the
sweep table. `chosen reward` and `rejected reward` are `beta` times how much
more likely the policy has made each answer than the reference did, and at
every learning rate **both are negative**:

```
lr 2.0e-5:  chosen -0.6595   rejected -0.8270   margin +0.1674
```

At beta = 0.1 that means the policy has made the *preferred* answers about
`e^6.6` — roughly 700x — less likely than the model it started from, and the
rejected ones about 3,700x less likely. The margin is positive, so the loss is
happy. What is actually happening is that the model is pushing probability
away from both answers and merely pushing harder on one of them.

This is the known failure mode of DPO, and it is the reason
`ashugpt/eval/preference.py` reports the two rewards separately instead of
only their difference: in the difference, it is invisible.

## And then the same trap as §10.6, wearing different clothes

Every ranking metric above puts 2.0e-5 first. So all three checkpoints went
through `scripts/eval_instruction_following.py` — the same script, the same
held-out Dolly split, the same settings as every row in
[instruction-tuning.md](instruction-tuning.md):

| checkpoint | Dolly held-out loss | stop rate | mean tokens | loop rate |
| --- | ---: | ---: | ---: | ---: |
| `sft` — where DPO started | **2.7444** | 92% | 62 | 20% |
| DPO, 1.0e-6 | 2.7504 | **98%** | 70 | **18%** |
| DPO, 5.0e-6 | 2.7661 | **98%** | 79 | 22% |
| DPO, 2.0e-5 | 2.8008 | 88% | 103 | 40% |

The ranking inverts completely. **2.0e-5 — best on every preference metric —
is the only checkpoint that is worse than the model it started from on every
behavioural one**: it stops less often than the SFT baseline (88% against
92%), its answers grow by two thirds (62 → 103 tokens), and its loop rate
doubles (20% → 40%). That is degeneration, and it is exactly what the sign of
the implicit rewards predicted one section ago: a model that has pushed 700x
of probability mass away from the answers a human preferred has not become
more agreeable, it has become less certain of everything.

1.0e-6, which looked like the weakest run in the sweep — smallest margin,
lowest DPO accuracy, a held-out loss that never got below 0.666 — is the only
one that improves the model. Stop rate 92% → 98%, loop rate 20% → 18%, Dolly
held-out loss up by 0.006, which is noise at this scale.

This is the fourth time in this project that the metric closest to what is
being trained on has ranked checkpoints in the reverse of the order that
matters — after the behavioural metrics in
[§10.4](../README.md#104-what-it-changed-measured), early stopping in the same
section, and stage 1's own held-out loss in
[§10.6](../README.md#106-sequence-packing--the-89-that-was-padding). The
pattern is consistent enough now that it should probably be the default
assumption rather than a recurring surprise: **a preference run has to be
scored on something that is not the preference objective.**

## What ships, and the epoch that did not

The sweep says 1.0e-6. So the obvious next move was to run it properly — a
full epoch, 1,495 steps, one complete cosine cycle, about an hour
(`logs/dpo_hh.csv`). Its held-out DPO loss falls the entire way, 0.6931 to
0.6491, and is still drifting down at the last eval. Every instinct says train
longer.

Scored on everything else, the extra 1,095 steps do nothing:

| | DPO accuracy | per-token accuracy | Dolly loss | stop rate | mean tokens | loop rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `sft` | 50.0% | 54.3% | **2.7444** | 92% | **62** | 20% |
| 400 steps — **shipped** | 57.1% | 55.2% | 2.7504 | **98%** | 70 | **18%** |
| 1,495 steps | **58.4%** | **55.7%** | 2.7595 | **98%** | 77 | 25% |

1.3 points of the metric being optimized. 0.5 points of ranking-by-content,
all of which arrives by step 598 and none after. And a slow drift the wrong way
on everything else: the answers keep lengthening, and the loop rate finishes
above the untuned baseline it started below.

I want to be careful about how strong that claim is. The behavioural gaps are
small, and loop rate is 40 sampled generations — 18% against 25% is seven
generations against ten. This is not "the long run is worse". It is "the long
run costs 3.7x the compute and shows no benefit outside the objective it was
trained on", which is enough to ship the short one and say why.

The one number that does keep climbing through those 1,095 steps is the margin,
+0.0505 to +0.0935. That is not skill. That is distance from the reference, and
the two rewards say what the distance is made of: at step 1,495 the model has
made the *preferred* answers about 12x less likely than the model it started
from, and the rejected ones 45x less likely. The margin grows because the
retreat is uneven, not because the model is getting better at anything.

## The length-normalized variant, which was supposed to fix this

If the model is a ruler, stop measuring in a unit that rewards brevity: divide
each sequence's log-probability by its own supervised-token count and the
length term leaves the objective. That is `length_normalized=True` in
`DPOModel.forward`, and it is a different algorithm rather than a scaling
detail — the implicit reward it defines is a different quantity.

`beta` therefore does not carry over. Normalizing divides every term by ~80
tokens, so the margins are ~80x smaller and 0.1 would be an ~80x weaker pull.
Two points, 400 steps each, identical to the shipped run otherwise: **8.0**,
which is 0.1 × 80 and restores the gradient's size
(`configs/train/dpo_hh_ln80.yaml`), and **1.0**, a gentler 10x correction
(`dpo_hh_ln10.yaml`), because a matched beta restores the size but not the
shape and two points bracket which of those mattered.

All four checkpoints are then scored by the *standard*, un-normalized
objective at beta 0.1 against the same frozen reference — one ruler for
everything, so the table compares weights rather than objectives:

| checkpoint | raw accuracy | chosen shorter | chosen longer | per-token accuracy | DPO accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sft` | 46.3% | 92.8% | 8.3% | 54.3% | 50.0% |
| standard, beta 0.1 | 46.4% | 92.9% | 8.4% | 55.2% | 57.1% |
| length-normalized, beta 1.0 | **46.5%** | 93.1% | 8.4% | **55.9%** | 56.5% |
| length-normalized, beta 8.0 | **46.5%** | 93.0% | 8.4% | 54.9% | **57.9%** |

It did not fix the shortcut. Raw accuracy moves 46.3% → 46.5%, and the split
that diagnosed the problem ends at 93.0% / 8.4% against the SFT model's 92.8%
/ 8.3% — the same ruler, marginally more so, after training with the length
term removed from the objective entirely.

The reason is worth more than the experiment, and I did not see it until the
numbers came back. Raw ranking accuracy scores a model by the summed
log-probability it assigns a sequence, and that sum is negative and grows with
length regardless of the weights. Taking the length term out of the *training
objective* changes where the weights go; it cannot change the fact that the
*metric* charges 2-3 nats a token. The shortcut lives in the ruler, not in the
model — so no objective was ever going to move that column, and the thing I
called a failure of standard DPO in the section above is partly a property of
how I chose to score it. Per-token accuracy is the column that already scores
differently, and it is where the variant shows up: 55.9% against standard
DPO's 55.2%, off an SFT baseline of 54.3% — about 1.8x the movement standard
DPO managed, which is real and small.

The two betas split, the way this project keeps splitting. beta 8.0 wins the
trained objective (57.9%, best of the four) and is the worst of the three
tuned checkpoints at ranking by content (54.9%, below standard DPO). beta 1.0
is the exact reverse. The metric closest to what the run optimized ranks them
backwards again — the fifth instance in this project.

Behaviour, on the same held-out Dolly split every other stage is scored on:

| checkpoint | Dolly held-out loss | stop rate | mean tokens | loop rate |
| --- | ---: | ---: | ---: | ---: |
| `sft` | **2.7444** | 92% | **62** | 20% |
| standard, beta 0.1 | 2.7504 | **98%** | 70 | **18%** |
| length-normalized, beta 1.0 | 2.7561 | 88% | 86 | **18%** |
| length-normalized, beta 8.0 | 2.7459 | **98%** | 58 | 22% |

beta 1.0 ranks content best and damages the model most — 88% stop rate against
92%, answers 39% longer. beta 8.0 costs 0.0015 nats, which is nothing, and is
the only checkpoint here whose answers get *shorter*, against a stage that is
known to push the other way. That is beta holding the policy near the
reference, not length normalization doing anything: both implicit rewards
still go negative under normalization at both settings, so the KL retreat is
untouched and remains a separate problem.

Nothing here ships. `configs/train/dpo_hh.yaml` is still the default — the
variant was run against a specific diagnosed failure, did not fix it, and the
metric it did improve moved 0.7 points at the cost of either behaviour or
content ranking depending on which beta you take. Both configs are in the tree
so the runs reproduce.

## What I would do differently

**Use a preference set whose chosen answers are not systematically longer.**
The 80-vs-73-token gap in HH-RLHF hands the model a shortcut that is worth more
nats than any content signal in the data, and everything downstream is measured
through it. UltraFeedback is already wired up in
`scripts/prepare_preference_data.py` and has the opposite problem (very long
answers on both sides), which is at least a different problem.

**~~Try length-normalized DPO.~~** Done, in the section above. It did not fix
what it was aimed at, and the reason it could not is the more useful half of
the result.

**Raise beta before lowering the learning rate.** Both implicit rewards going
negative is a KL problem, not a step-size problem, and beta is the knob that
addresses it directly. The sweep varied the learning rate because that is the
habit this repo has built up over three previous stages; beta = 0.2 or 0.5 with
the same schedule is the experiment that was not run.

## Reproducing this

```bash
python scripts/prepare_preference_data.py --dataset hh --output data/preference/hh.jsonl
python scripts/prepare_preference_data.py --dataset hh --split test --output data/preference/hh_test.jsonl

python scripts/preference_tune.py --model configs/model/medium.yaml \
    --train configs/train/dpo_hh.yaml \
    --init-from checkpoints/sft_dolly_packed3e5/step_940.pt \
    --data data/preference/hh.jsonl \
    --checkpoint-dir checkpoints/dpo_hh --log-path logs/dpo_hh.csv

# ranking, on a split the run never saw
python scripts/eval_preference.py --data data/preference/hh_test.jsonl \
    --reference checkpoints/sft_dolly_packed3e5/step_940.pt \
    --checkpoint sft=checkpoints/sft_dolly_packed3e5/step_940.pt \
    --checkpoint dpo=checkpoints/dpo_hh/step_400.pt \
    --output results/preference_eval_hh.md

# behaviour, on the same held-out Dolly split every other stage is scored on
python scripts/eval_instruction_following.py --data data/sft/dolly.jsonl \
    --loss-batches 34 \
    --checkpoint sft=checkpoints/sft_dolly_packed3e5/step_940.pt \
    --checkpoint dpo=checkpoints/dpo_hh/step_400.pt \
    --output results/instruction_eval_dpo.md
```

The learning-rate sweep is `logs/dpo_hh_lr{1e6,5e6,2e5}.csv` — the same command
with `max_lr`/`min_lr` changed and `max_steps` left at 400 — and its scored
output is [`preference_eval_sweep.md`](preference_eval_sweep.md) and
[`instruction_eval_dpo_sweep.md`](instruction_eval_dpo_sweep.md).

The length-normalized runs are the same training command against a different
config, and the two are scored together with the standard checkpoint so that
one ruler covers all four:

```bash
python scripts/preference_tune.py --model configs/model/medium.yaml \
    --train configs/train/dpo_hh_ln80.yaml \
    --init-from checkpoints/sft_dolly_packed3e5/step_940.pt \
    --data data/preference/hh.jsonl \
    --checkpoint-dir checkpoints/dpo_hh_ln80 --log-path logs/dpo_hh_ln80.csv

python scripts/eval_preference.py --data data/preference/hh_test.jsonl \
    --reference checkpoints/sft_dolly_packed3e5/step_940.pt \
    --checkpoint sft=checkpoints/sft_dolly_packed3e5/step_940.pt \
    --checkpoint dpo_standard=checkpoints/dpo_hh_lr1e6/step_400.pt \
    --checkpoint dpo_ln_b1=checkpoints/dpo_hh_ln10/step_400.pt \
    --checkpoint dpo_ln_b8=checkpoints/dpo_hh_ln80/step_400.pt \
    --output results/preference_eval_length_normalized.md
```

`dpo_hh_ln10.yaml` is the beta 1.0 point, writing `logs/dpo_hh_ln10.csv`. Both
runs' traces, and the two scoring passes, are in `logs/dpo_ln.log`; the scored
tables are
[`preference_eval_length_normalized.md`](preference_eval_length_normalized.md)
and
[`instruction_eval_length_normalized.md`](instruction_eval_length_normalized.md).
