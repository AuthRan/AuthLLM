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
rather than a vibe. So `scripts/eval_instruction_following.py` takes the same
300 held-out Dolly examples the fine-tune never saw and measures, for each
checkpoint: held-out loss on the response only, how often the model stops on
its own, how long its answers are, and how often it falls into a loop.

## The table

40 held-out prompts generated at temperature 0.8, top-k 50, capped at 200
tokens; loss averaged over 34 batches of the same held-out split.

| checkpoint | held-out loss | ppl | stop rate | mean tokens | loop rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| `base` (pretrained, step 20,000) | 3.0580 | 21.28 | 30% | 179 | 80% |
| `alpaca` step 500 | 2.9049 | 18.26 | 98% | 58 | 15% |
| `alpaca` step 1,500 | 2.9506 | 19.12 | 100% | 50 | 10% |
| `alpaca` step 4,500 | 3.0844 | 21.85 | 100% | 51 | 0% |
| `dolly` 1 epoch | 2.8183 | 16.75 | 98% | 44 | 12% |
| `dolly` 2 epochs | 2.7988 | 16.42 | 100% | 52 | 15% |
| `dolly` 3 epochs | 2.7921 | 16.31 | 100% | 52 | 18% |

Three things in there are worth more than the rest.

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

Look at the `alpaca` rows again:

| | held-out loss | stop rate | loop rate |
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

## 3. The second stage didn't make it better, it moved where it was good

Dolly is human-written where Alpaca is machine-generated, so stage two was
supposed to be about answer quality. Held-out Dolly loss does improve with
every epoch of it — 2.8183, 2.7988, 2.7921 — and if that were the only number
I'd looked at, the conclusion would have been "more Dolly is better, keep
going".

So I scored the same checkpoints a second time, on **Alpaca's** held-out
split, which no stage of training saw either:

| checkpoint | Dolly held-out | Alpaca held-out | mean |
|---|---:|---:|---:|
| `base` | 3.0580 | 2.5338 | 2.7959 |
| `alpaca` step 1,500 | 2.9506 | **2.0568** | 2.5037 |
| `alpaca` step 4,500 | 3.0844 | 2.0752 | 2.5798 |
| `dolly` 1 epoch | 2.8183 | 2.1211 | 2.4697 |
| `dolly` 2 epochs | 2.7988 | 2.1311 | **2.4650** |
| `dolly` 3 epochs | **2.7921** | 2.1427 | 2.4674 |

Read down the Dolly rows. Every epoch improves the Dolly column and degrades
the Alpaca column by almost exactly the same amount: one epoch to three is
−0.026 on Dolly and +0.022 on Alpaca. That is not a model learning to follow
instructions better. That is a model sliding from one instruction
distribution toward another, at close to a 1:1 exchange rate, and there is no
way to see it with a single held-out set — which is the whole reason I ran
the second one.

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

The wrong prediction is still in the repo, in
`configs/train/sft_dolly_1epoch.yaml`, with a note at the top explaining what
overturned it. It is more useful to me there than deleted.

## What it did not change

Everything I said about the base model's grasp of facts in
[README.md](README.md) is still true afterwards. Instruction tuning taught it
to **answer**; it did not teach it to **know**.

> **What are brambles?**
>
> *base:* (writes more instructions)
>
> *alpaca:* Brambles are small objects that have a shape made up of a
> single-ingredient element. They are usually made of glass, metal, or
> anything else that can absorb and release energy when pushed.
>
> *dolly:* Brambles, also known as plasticizers, are chemical compounds that
> are added to a mixture of water and water particles to create a slurry.

Brambles are thorny shrubs. Both fine-tuned answers are the right length, in
the right register, with the right confident encyclopaedic cadence, and both
are inventions. The fine-tune moved the model from "cannot participate in the
conversation" to "participates fluently and is wrong", which is about what 11
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
then thrown away by the mask, or attention over padding. The whole Alpaca
stage is 9.0M supervised tokens and the Dolly stage 2.0M, delivered at the
cost of 80M and 15M token-positions of compute respectively.

Fixed padding was the right call for the first run — it keeps the tensor shape
byte-identical to pretraining, so the measured 6.01GB peak carried over with
no new memory work — but it is the obvious thing to fix next. Packing several
short examples into one 512-token window (with attention masked at the
boundaries so they can't see each other) would recover most of that 4-5x.

## Reproducing this

```bash
python scripts/eval_instruction_following.py \
    --data data/sft/dolly.jsonl \
    --checkpoint base=checkpoints/medium/step_20000.pt \
    --checkpoint alpaca-1500=checkpoints/sft_alpaca/step_1500.pt \
    --checkpoint dolly-2ep=checkpoints/sft_dolly/step_940.pt \
    --output results/instruction_eval_dolly.md

# the cross-check, same checkpoints, the other held-out set:
python scripts/eval_instruction_following.py --data data/sft/alpaca.jsonl ... \
    --output results/instruction_eval_alpaca.md
```

Every generation behind the tables is in
[instruction_eval_dolly.md](instruction_eval_dolly.md) and
[instruction_eval_alpaca.md](instruction_eval_alpaca.md) — 40 prompts per
checkpoint, unedited and uncherry-picked. The held-out splits are
reconstructed from the same seed the fine-tune used rather than stored, so
those examples are genuinely ones no stage of training ever saw.
