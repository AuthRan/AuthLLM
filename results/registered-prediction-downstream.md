# Registered prediction: does retuning survive a metric that is not same-corpus loss?

**Written 2026-08-30, before any run of this experiment existed.**
`results/lr_scaling_downstream.csv` does not exist as this is saved, and no
checkpoint of any of the three conditions has been kept before now — the sweep
runs with `--keep-checkpoints` off by default, so every fine-tune this project
has done was deleted after its loss was recorded.

**Drafted by the assistant and not reviewed by the author before registration.**
Recorded now because a prediction written after the runs land is worth nothing.
Strike it if you disagree; that costs nothing.

This is the seventh registered prediction in this project, and the first that is
not about the exponent.

## Why this and not more seeds

Section 7 lists four things that would move this work forward, and three of them
need hardware or data this machine does not have: models above 124M, corpora
drawn independently rather than sliced from two, and batches near production
scale. The fourth is **a downstream metric to sit beside held-out loss**, and it
needs neither. Section 5 already concedes the gap:

> The learning-rate optimum reported here is the optimum *for held-out
> validation loss on the same corpus*, which is the right target for a scaling
> question and is not the same as the optimum for a downstream pipeline.

That is a limitation the paper states and does not test. It is testable here for
about half an hour of one GPU, and it can fail.

## What is being run

Section 4.3's three conditions, at 124M on Alpaca at a matched data budget of one
epoch, seed 1337, re-run with their checkpoints kept:

| | cell | peak lr | what it is |
| --- | --- | ---: | --- |
| **A** | `unpacked_1600` | 3e-5 | padded at its optimum |
| **B** | `packed_350` | 3e-5 | packed at the *inherited* rate |
| **C** | `packed_350` | 1.5e-4 | packed at its *own* optimum |

Section 4.3 reports these at 2.0720, 2.0678 and 2.0175 nats of held-out Alpaca
loss, and concludes that retuning is worth 0.050 nats against inheriting while
inheriting is a wash against not packing.

Each checkpoint is then scored by `scripts/eval_instruction_following.py` on:

1. **Alpaca held-out loss** — the same measure section 4.3 used, as a
   reproduction check.
2. **Dolly held-out loss** — the same three models, scored on a corpus none of
   them was fine-tuned on. This is the out-of-distribution measure.
3. **Generation behaviour** on held-out Alpaca prompts: stop rate, mean tokens,
   loop rate.

The base model (`checkpoints/medium/step_20000.pt`) is scored alongside as a
reference column, because a behavioural number means nothing without it.

## The prediction of record

**H1 — retuning is a real quality gain and not an artefact of scoring on the
corpus that was trained on.** Two conditions, both required:

1. **Out of distribution.** C's Dolly held-out loss is **below B's by more than
   0.010 nats**. The margin is set at a fifth of the in-distribution gain that
   section 4.3 reports (0.050), and comfortably above the 0.004 that section 4.3
   itself calls a wash.
2. **Behaviour.** C's stop rate on held-out Alpaca prompts is **not below B's**.
   C is trained at five times B's learning rate and should be at least as far
   from the base model's never-emit-EOS behaviour, not less far.

**Point estimate.** I expect C to beat B on Dolly by 0.01 to 0.03 nats — a real
gap, smaller than the 0.050 in-distribution one, because the shared component of
instruction-following transfers and the Alpaca-specific component does not. On
stop rate I expect C well above B, and B only a little above the base model.

**What would falsify it.** C losing to B on Dolly held-out loss. That would mean
the higher learning rate buys same-corpus loss at the cost of generalisation,
and that section 4.3's conclusion is a statement about one metric rather than
about quality. **That outcome is more interesting than confirmation and must be
reported in section 4.3 and section 5 if it happens**, not buried. A gap between
-0.010 and +0.010 nats is a null, not a pass: it would mean the two conditions
are indistinguishable out of distribution and that section 4.3's 0.050 nats does
not transfer.

**A null of power is not a pass.** If all three conditions land within a few
thousandths of each other on Dolly, or if the generations are too degenerate for
stop rate to separate anything, that is a failure to measure and is reported as
one.

## Validity check, which comes first

The three runs are re-runs of configurations this project has already done, at
the same seed. Their Alpaca held-out losses should reproduce section 4.3's
2.0720, 2.0678 and 2.0175. Some drift is expected — fp16 accumulation is not
bitwise deterministic across runs — but **if any of the three differs from its
recorded value by more than 0.005 nats, the re-run is not the same experiment
and this whole test is void** until that is explained. Recorded here so that a
disagreement cannot be waved through afterwards.

Note that the eval script's held-out loss is averaged over `--loss-batches`
batches rather than the fine-tune's full validation pass, so its absolute value
may sit slightly off the ledger's; the check above is against the ledger's own
`final_val_loss` for the same three runs, which the sweep records independently.

## Limitations recorded in advance

1. **One training seed.** All three conditions are seed 1337, the seed section
   4.3 itself is restricted to and for the reason given in section 3.1. So this
   inherits section 4.3's limitation exactly: no cross-seed bound stands behind
   any single number here. What it adds is a second and a third *metric*, not a
   second seed.
2. **Dolly is not a downstream task.** It is a second instruction corpus, so
   this measures transfer between instruction distributions and not performance
   on anything a user would ask for. It is the strongest out-of-distribution
   measure available on this machine and it is weaker than the thing section 7
   asks for.
3. **Stop rate and loop rate are behaviour, not quality.** A model can stop
   promptly and be wrong. They are reported because they are the clearest
   base-versus-tuned signals this project has measured, not because they rank
   quality.
4. **Generation is sampled** at temperature 0.8, so the behavioural numbers
   carry sampling noise on 40 prompts. Differences under a few points of stop
   rate should not be read.
5. **One corpus fine-tuned, one model size.** Alpaca at 124M. If it holds, the
   Dolly-tuned mirror of it is the obvious next run and is not done here.

## How it will be scored

```
python scripts/eval_instruction_following.py --data data/sft/alpaca.jsonl ...
python scripts/eval_instruction_following.py --data data/sft/dolly.jsonl  ...
```

with the same `--split-seed 1337` and `--val-fraction 0.02` the fine-tunes used,
and every checkpoint passed in one invocation so all columns are scored on the
same held-out examples. The two conditions above are then read off the Dolly
table and the Alpaca generation table. No discretion.

---

# Scoring, 2026-08-30

**H1 is falsified. The gain section 4.3 measures does not transfer, and it
reverses sign.**

The validity check passes at full precision: all three conditions reproduce
section 4.3's losses to **0.0000 nats** — 2.0720, 2.0678, 2.0175. The
registration allowed 0.005 before declaring the test void, expecting fp16
accumulation to be non-deterministic across runs; on this hardware it is not.

| | Alpaca held-out | Dolly held-out | stop rate | mean tokens | loop rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| base | 2.5711 | 3.0653 | 30% | 182 | 88% |
| **A** padded at its optimum | 2.0720 | 2.9518 | 98% | 44 | 10% |
| **B** packed, inherited rate | 2.0678 | **2.8873** | 95% | 50 | 18% |
| **C** packed, retuned rate | **2.0175** | 2.9739 | 98% | 51 | 12% |

Alpaca is the corpus all three were fine-tuned on. Dolly is not, and all four
columns were scored in one invocation on the same held-out examples.

**Condition 1 of record — falsified.** The criterion was that C beat B on Dolly
by more than 0.010 nats. **C is worse than B by 0.087.**

**Condition 2 of record — passes.** C's stop rate is 98% against B's 95%, so it
is not below. The registration says differences of a few points on 40 prompts
should not be read, and this is one, so it passes without meaning much.

## What this says

The same comparison reverses sign between the two corpora:

* on Alpaca, C beats B by **0.050 nats** — section 4.3's number, reproduced;
* on Dolly, C loses to B by **0.087 nats**.

The reversal is larger than the gain. And the ranking on Dolly puts **B first of
the three**: the condition section 4.3 describes as collecting "none of the
quality packing had available" is the one that generalises best, and the retuned
run that wins on the tuning corpus comes last of the three fine-tunes.

That is what the registration named as the falsifying outcome and said would be
"more interesting than confirmation". It is: it means **section 4.3's 0.050 nats
is a statement about held-out loss on the corpus that was tuned on, and not a
statement about quality.** Section 5 already said the optimum reported in this
paper is the optimum for same-corpus held-out loss and "is not the same as the
optimum for a downstream pipeline". This is that caveat measured rather than
asserted, and it turns out to bite harder than the wording suggested.

A mechanism is available and is not tested here: C trains at five times B's
learning rate, so it moves further from the base model and specialises harder on
the corpus it sees. Nothing here separates "specialised harder" from "generalises
worse"; they are the same observation twice.

## What this does not say

**It does not touch the paper's central claim.** Sections 4.1 through 4.7 are
about *where the optimum of held-out loss on the tuning corpus sits*, and that is
the right target for a scaling question and is unaffected by this. Retuning still
moves the optimum by 1.7x to 12.7x, still recovers 0.050 to 0.172 nats on the
corpus being tuned, and the mechanism in 4.4 is untouched.

What changes is the practical framing in 4.3 and 6. A practitioner who packs and
retunes gets a better model *on their own held-out split*, and this experiment
says they should not assume that carries to a different distribution.

## Limitations, as registered

1. **One training seed.** All three conditions are seed 1337, inheriting section
   4.3's limitation exactly. A 0.087-nat reversal is large next to the 0.050 it
   reverses, but no cross-seed bound stands behind either.
2. **Dolly is a second instruction corpus, not a downstream task.** This measures
   transfer between instruction distributions. It is the strongest
   out-of-distribution measure available here and it is weaker than what section
   7 asks for.
3. **One corpus fine-tuned, one model size.** The Dolly-tuned mirror -- does the
   Dolly-retuned model lose on Alpaca? -- is the obvious next run and is not
   done. If the reversal is symmetric it is about specialisation; if it is not,
   something else is going on.
4. Stop rate, mean tokens and loop rate are behaviour and not quality, and all
   three fine-tunes are far from the base model on all three.
