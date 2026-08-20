# What Instruction Tuning Actually Changed

The base 124M model does not answer questions. That sentence is easy to write
and easy to nod along to, and it took me a while to appreciate how literally
true it is. Here is the base model, given "Write a tribute to my high school
swim coach" inside the standard Alpaca prompt template:

> Describe some actions that would be easy to perform for the swim coach.
> ### Refine:

It didn't write a bad tribute. It wrote **another instruction**. Given a
document that contains one instruction, the most likely continuation in
FineWeb-Edu is more of the same kind of text, and that is exactly what it
produced. Asked who Luke Skywalker's parents are, it answered — and then kept
going, generating `### Response:` headers and more questions for itself,
forever, until it hit the token cap.

That is the thing instruction tuning fixes, and I wanted to see it as a number
rather than a vibe. So `scripts/eval_instruction_following.py` takes held-out
instruction data neither stage ever trained on and measures, for each
checkpoint: held-out loss on the response only, how often the model stops on
its own, how long its answers are, and how often it falls into a loop.

The shipped pipeline is two stages — 1,600 steps on Alpaca, then 940 on Dolly
— but seven other checkpoints ran on the way there, and the interesting part
of this page is the four of them that were *supposed* to be better. A later
pass at making the stages cheaper produced a fifth, which is the last section.

## The table

Every checkpoint that ran, scored the same way. Held-out loss on Dolly's split
(300 examples) and on Alpaca's (1,039), both with the prompt masked exactly as
in training, so the number scores prediction of the *response* only. The
behavioural columns come from 40 real sampled generations per checkpoint at
temperature 0.8, top-k 50, capped at 200 tokens.

| checkpoint | Dolly loss | Alpaca loss | mean | stop rate | mean tokens | loop rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `base` — pretrained, step 20,000 | 3.0580 | 2.5338 | 2.7959 | 30% | 179 | 80% |
| 3-epoch Alpaca, step 500 | 2.9049 | 2.0973 | 2.5011 | 98% | 58 | 15% |
| 3-epoch Alpaca, step 1,500 | 2.9506 | 2.0568 | 2.5037 | 100% | 50 | 10% |
| 3-epoch Alpaca, step 4,500 | 3.0844 | 2.0752 | 2.5798 | 100% | 51 | 0% |
| **stage 1** — 1-epoch Alpaca, step 1,600 | 2.9145 | **2.0512** | 2.4829 | 100% | 47 | 15% |
| Dolly 1 epoch, off step 1,500 | 2.8183 | 2.1211 | 2.4697 | 98% | 44 | 12% |
| Dolly 2 epochs, off step 1,500 | 2.7988 | 2.1311 | 2.4650 | 100% | 52 | 15% |
| Dolly 3 epochs, off step 1,500 | 2.7921 | 2.1427 | 2.4674 | 100% | 52 | 18% |
| **stage 2** — Dolly 2 epochs, off step 1,600 | **2.7707** | 2.1365 | **2.4536** | 98% | 52 | 15% |

Compare *down* a column, never across one row: Alpaca's machine-generated
responses are far more predictable than Dolly's human-written ones, which is
why the base model scores half a nat better on one than the other before any
tuning happens at all.

Four things in there are worth more than the rest.

## 1. Stopping is learned in under 500 steps

Stop rate goes 30% → 98% after the first 500 optimizer steps of fine-tuning,
and mean answer length collapses from 179 tokens (i.e. "ran to the cap") to
58. Nothing else in the run moves that fast.

It's the cheapest thing to learn because it's the most consistent signal in
the data: **every single training example ends with `<|endoftext|>` after the
response**. The model isn't learning a concept of "done", it's learning that
this template's response field is followed by that token, and 50,000
consistent demonstrations of a single token in a single position is an
extremely easy thing to fit.

The base model's 30% is more interesting than it looks. It isn't stopping on
purpose — FineWeb-Edu documents are separated by `<|endoftext|>`, so the base
model does emit it occasionally, wherever it decides the "document" it is
hallucinating has ended. Which is why the 30% comes with an 80% loop rate:
those two numbers describe the same model wandering.

## 2. More fine-tuning made it worse in the way that matters

Look at the three-epoch Alpaca rows again:

| | Dolly held-out | stop rate | loop rate |
|---|---:|---:|---:|
| step 500 | **2.9049** | 98% | 15% |
| step 1,500 | 2.9506 | 100% | 10% |
| step 4,500 | 3.0844 | 100% | **0%** |

Read the behaviour columns and step 4,500 is the best model in the table: it
always stops, it never loops. Read the loss column and step 4,500 is **worse
than the base model it started from** — 3.0844 against 3.0580, on held-out
data from a different instruction set.

Both readings are true. Three epochs on Alpaca produced a model that has
perfectly memorized the *shape* of an Alpaca answer and generalizes worse to
anyone else's instructions than the raw pretrained model does. It never loops
because it has learned to emit a confident short paragraph and stop, whatever
the question:

> **Who is Luke Skywalker's parents?**
> Luke Skywalker's parents are Luke Skywalker and Sarah.

Ten tokens, fluent, stops cleanly, completely wrong, and delivered with total
confidence. The behavioural metrics have no way to see that.

This is the clearest thing I've measured in this project about why you cannot
evaluate an instruction-tuned model on format alone. If I'd only tracked stop
rate and loop rate — which are the metrics that *look* like they measure
instruction following — I'd have shipped the worst checkpoint of the three.

## 3. A short schedule beats the same step of a long one

The obvious fix for §2 is "train long, keep the best checkpoint", and the
three-epoch run's best checkpoint is step 1,500 — one epoch, near enough. So
that seemed settled, and it wasn't. Re-running stage 1 as a **1,600-step
schedule that actually finishes** gives a better model than that checkpoint on
both held-out sets at once, for a third of the compute:

| | Dolly held-out | Alpaca held-out | GPU time |
|---|---:|---:|---:|
| step 1,500 of the 3-epoch run | 2.9506 | 2.0568 | 55 min |
| the 1,600-step run, finished | **2.9145** | **2.0512** | 18 min |

The GPU-time column counts the whole run, because that is what it costs to
have that checkpoint: step 1,500 arrives 17 minutes in, but you only learn it
is the one worth keeping after the other 3,375 steps have gone by.

The two checkpoints are nearly the same number of optimizer steps over nearly
the same data. What differs is the learning rate at the end. Step 1,500 of the
long run sits mid-cosine at lr ~1.7e-5 and never receives the annealing that
does the last of the work; the short run decays to `min_lr` and lands. "Train
long, keep the best checkpoint" and "train exactly as long as you need" look
like the same experiment when you plot them against step number, and they are
not.

That difference compounds through stage 2. Same 940-step Dolly config, same
everything, only the starting checkpoint changed:

| stage 2 started from | Dolly held-out | Alpaca held-out | mean |
|---|---:|---:|---:|
| 3-epoch run's step 1,500 | 2.7988 | 2.1311 | 2.4650 |
| the finished 1,600-step run | **2.7707** | 2.1365 | **2.4536** |

0.028 nats at the end of a stage that had nothing to do with the change. A
better starting point stays better through the stage built on top of it, which
is an argument for spending the effort on stage 1 rather than tuning stage 2
harder.

## 4. The second stage didn't make it better, it moved where it was good

Dolly is human-written where Alpaca is machine-generated, so stage two was
supposed to be about answer quality. Held-out Dolly loss does improve with
every epoch of it — 2.8183, 2.7988, 2.7921 — and if that were the only number
I'd looked at, the conclusion would have been "more Dolly is better, keep
going".

So I scored the same checkpoints a second time, on **Alpaca's** held-out
split, which no stage of training saw either. Read down the Dolly rows in the
big table. Every epoch improves the Dolly column and degrades the Alpaca
column by almost exactly the same amount: one epoch to three is −0.026 on
Dolly and +0.022 on Alpaca. That is not a model learning to follow
instructions better. That is a model sliding from one instruction distribution
toward another, at close to a 1:1 exchange rate, and there is no way to see it
with a single held-out set — which is the whole reason I ran the second one.

It also settles the schedule question I'd been arguing with myself about.
`configs/train/sft_dolly.yaml` originally ran one epoch, reasoning by analogy:
the Alpaca stage overfit within a single pass, so a second pass over a set a
quarter the size should overfit harder. The validation curve disagreed at
every single eval point. I ran the sweep properly — 470, 940 and 1,410 steps,
each a complete cosine cycle so the endpoints are comparable:

| schedule | best Dolly val | at step |
|---|---:|---:|
| 1 epoch (470) | 2.8237 | 470, still falling |
| 2 epochs (940) | 2.8038 | 900 |
| 3 epochs (1,410) | **2.7969** | 1,300 |

The minimum is real and sits around three epochs — step 1,410 is the first
tick upward anywhere in the sweep. But epoch two buys 0.0199 nats and epoch
three buys 0.0069, and epoch three is also the worst of the three on Alpaca.
The shipped config runs 940 steps, which is where the mean of the two
held-out losses bottoms out. That's the least arbitrary stopping point I can
justify once the trade is visible, and I'd rather ship a defensible number
than the one that wins the column I happened to plot first.

That sweep ran on the older stage-1 lineage, before §3 was known. Its
conclusion carried over unchanged — the curve off the new stage 1 has the same
shape and the same minimum, bottoming at step 900 and ticking up by 940 — so
it was not re-run.

Both wrong predictions are still in the repo, in `sft_alpaca_3epoch.yaml` and
`sft_dolly_1epoch.yaml` under `configs/train/`, each with a note at the top
explaining what overturned it. They are more useful to me there than deleted.

## What it did not change

Everything I said about the base model's grasp of facts in
[README.md](README.md) is still true afterwards. Instruction tuning taught it
to **answer**; it did not teach it to **know**.

> **What are brambles?**
>
> *base:* (writes more instructions)
>
> *stage 1:* Brambles are small objects that have a shape made out of a
> material that is used in a machine. Depending on the type of object, the
> materials can vary, but common materials include plastics, metal, and glass.
>
> *stage 2:* Brambles, also known as plasticizers, are chemical compounds that
> are added to a mixture of non-cemented polymers. They are also an essential
> part of a good manufacturing process.

Brambles are thorny shrubs. Both fine-tuned answers are the right length, in
the right register, with the right confident encyclopaedic cadence, and both
are inventions. The fine-tune moved the model from "cannot participate in the
conversation" to "participates fluently and is wrong", which is about what 5.0
million supervised tokens can buy on top of 2.46B pretraining tokens, and no
more than that.

Facts live in the pretrained weights. Fine-tuning at this scale rearranges how
they come out; it does not add any.

## A footnote on where the compute went

Both stages pad every example to a fixed 512 tokens, so each optimizer step
processes 16,384 token positions. The Alpaca training set averages 113 real
tokens per example and 58 response tokens, which means that of those 16,384
positions:

- ~3,600 (22%) are real tokens; the rest is padding
- ~1,850 (11%) are *supervised* — the rest is padding plus the masked prompt

So roughly 89% of every fine-tuning step is spent computing gradients that are
then thrown away by the mask, or attention over padding. Stage 1 is 3.0M
supervised tokens and stage 2 is 2.0M, delivered at the cost of 26M and 15M
token-positions of compute respectively.

Fixed padding was the right call for the first run — it keeps the tensor shape
byte-identical to pretraining, so the measured 6.01GB peak carried over with
no new memory work. Then I went and fixed it, which is the next section.

## Packing the window, and the thing it taught me instead

Packing several short examples into one 512-token window — with attention
masked at the boundaries so they can't see each other, and RoPE positions
restarting per example — recovers almost exactly the 4-5x the arithmetic above
predicts. Measured on a real step rather than assumed: **4.40x the supervised
tokens per second for 1.02x the per-step cost** and +0.07GB peak. Alpaca's
50,868 usable examples go from 50,868 windows to 11,220, 98.8% full. One epoch
of stage 1 drops from 18 minutes to 4.5.

That is the boring half. The interesting half is that **a packed step is a
different step**, and I nearly shipped that mistake. It carries 4.5x the
supervised tokens, so an epoch is 350 steps instead of 1,600 — and at the
learning rate the old config used, packing made the model *worse*:

| stage 1, max_lr | packed | unpacked |
|---|---:|---:|
| 2.0e-5 | 2.0912 | **2.0745** |
| 3.0e-5 | 2.0678 | 2.0720 |
| 6.0e-5 | 2.0352 | 2.0973 |
| 9.0e-5 | 2.0217 | — |
| 1.5e-4 | **2.0175** | — |

Same data, same one epoch, better throughput, worse model — purely because the
schedule got silently rescaled underneath. I ran the unpacked column as a
control specifically to check the boring explanation, that packing was really
just fixing a badly tuned baseline. It isn't: unpacked peaks at 3e-5 and has
already turned by 6e-5, so the 2e-5 the stage shipped was about right for the
batch it had. The two optima sit about 5x apart against a 4.53x batch ratio,
which is linear scaling. I'd have guessed square-root, and square-root would
have stopped me less than halfway.

### And then the same trap as §2, wearing different clothes

The packed column keeps falling all the way to 1.5e-4. So take the best one,
obviously.

No. Feeding each of those checkpoints into the *identical* unchanged stage 2:

| stage 1 | Dolly | Alpaca | mean | → final model | Dolly | Alpaca | mean |
|---|---:|---:|---:|---|---:|---:|---:|
| packed 3.0e-5 | **2.8809** | 2.0431 | 2.4620 | from 3.0e-5 | **2.7444** | 2.1237 | 2.4341 |
| packed 9.0e-5 | 2.9230 | 1.9993 | 2.4612 | from 9.0e-5 | 2.7755 | 2.0768 | **2.4261** |
| packed 1.5e-4 | 2.9690 | **1.9958** | 2.4824 | — | | | |
| unpacked 2e-5 | 2.9145 | 2.0512 | 2.4829 | the shipped pipeline | 2.7707 | 2.1365 | 2.4536 |

Rank those three by the stage-1 Alpaca loss I'd have selected on — 9.0e-5 is
best, then 3.0e-5, then unpacked — and the finished models come out in exactly
the reverse order on Dolly. The best stage 1 by that metric gives the worst
final model. (1.5e-4 scores better still at stage 1 and I didn't run stage 2
from it at all: it's already the worst row in that table on Dolly, which is
the tell Alpaca's own split can't give you.)

Past about 3e-5, the extra learning rate stops teaching the model to follow
instructions and starts driving it into Alpaca's particular distribution —
and Alpaca's own held-out split cannot see that happening, because it is
drawn from the same distribution.

That's the third time this project has handed me the same lesson. §2: the
behavioural metrics peak on the checkpoint you least want. §3: the best
checkpoint of a long run loses to a short run that finished. And now: the
metric closest to what you're training on is the one least able to tell you
whether the stage was good for the pipeline it feeds. Every time, the fix was
to score against something the stage was not trained on.

### What ships

Both packed pipelines beat the unpacked one, and the two of them are tied to
0.0008 on the mean, which is noise. `sft_alpaca_packed.yaml` (3.0e-5) is the
default: it produces the best Dolly held-out loss anything in this repo has
reached, and Dolly is the distribution stage 2 exists to fit.
`sft_alpaca_packed_9e5.yaml` sits next to it because it wins the mean, which
is the tiebreak I used for `sft_dolly.yaml` and I'd rather not switch
tiebreaks when it suits me.

The honest reading of 9.0e-5's win: all of its edge on the mean is Alpaca loss
that stage 2 didn't wash out. That's either a better-preserved model or just a
model stage 2 moved less — which is §4's relocation effect, not a gain. Both
readings fit. So both configs ship with their numbers rather than one quietly
becoming the answer.

One caveat that cost me a full re-run: `--loss-batches` defaults to 40 and
every number on this page uses 34. The flag silently changes which subset
"held-out loss" means — at 40 the base model scores 3.0653 on Dolly instead of
3.0580 — so a report run at the default is not comparable to any table here.

## Reproducing this

```bash
.venv/bin/python scripts/eval_instruction_following.py \
    --data data/sft/dolly.jsonl \
    --checkpoint base=checkpoints/medium/step_20000.pt \
    --checkpoint alpacaS-1600=checkpoints/sft_alpaca/step_1600.pt \
    --checkpoint dollyS-2ep=checkpoints/sft_dolly/step_940.pt \
    --loss-batches 34 \
    --output results/instruction_eval_dolly.md

# the cross-check, same checkpoints, the other held-out set:
.venv/bin/python scripts/eval_instruction_following.py --data data/sft/alpaca.jsonl ... \
    --output results/instruction_eval_alpaca.md
```

`checkpoints/sft_alpaca/` and `checkpoints/sft_dolly/` are the shipped runs;
the superseded schedules keep qualified names (`sft_alpaca_3epoch`,
`sft_dolly_1epoch`, `sft_dolly_2epoch`, `sft_dolly_3epoch`), and the packed
lineage is `sft_alpaca_packed{,_9e5}` for stage 1 and
`sft_dolly_packed{3e5,9e5}` for what stage 2 made of them. The training logs
were written before that rename and still name the directories the runs
originally wrote to.

Every generation behind the tables is in
[instruction_eval_dolly.md](instruction_eval_dolly.md) and
[instruction_eval_alpaca.md](instruction_eval_alpaca.md) for the shipped
pipeline, and in [instruction_eval_packed_dolly.md](instruction_eval_packed_dolly.md)
/ [instruction_eval_packed_alpaca.md](instruction_eval_packed_alpaca.md) (the
stage-1 sweep) and
[instruction_eval_packed_final_dolly.md](instruction_eval_packed_final_dolly.md)
/ [instruction_eval_packed_final_alpaca.md](instruction_eval_packed_final_alpaca.md)
(the finished pipelines) — 40 prompts per checkpoint, unedited and
uncherry-picked. The held-out splits are
reconstructed from the same seed the fine-tune used rather than stored, so
those examples are genuinely ones no stage of training ever saw.
