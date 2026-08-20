# 5. Teaching It to Answer

The pretrained model was finished and it was, in the most literal sense,
useless as an assistant. Not bad at answering — incapable of the concept. Ask
it something and it writes more questions, because in the text it learned
from, that's what follows a question.

This file is what it took to change that, and what changed when it did.

## The core idea, in one line

Instruction tuning does not change the model, the loss, or the optimizer. It
is the same next-token prediction the pretraining run used. The only thing
that changes is **which tokens count as targets**.

Every training example becomes one document:

```
Below is an instruction that describes a task. Write a response that
appropriately completes the request.

### Instruction:
Write a tribute to my high school swim coach.

### Response:
<the human-written answer from the dataset><|endoftext|>
```

If you train on that naively, you teach the model to produce all of it —
including the instruction. Which means you have built a machine that invents
questions. So the instruction's positions get label `-100`, PyTorch's
`ignore_index`, and contribute no gradient at all. The model still *reads*
the instruction (it's in the input, attention sees every token of it), it
just never has to *produce* it.

That's it. That's the whole mechanism. One line:

```python
labels[: n_prompt - 1] = IGNORE_INDEX
```

The `- 1` cost me a minute of staring. Labels in this repo are pre-shifted —
`labels[t]` is the token that should come after `input_ids[t]` — so the first
position whose *target* is a response token is `n_prompt - 1`, not `n_prompt`.
Get it wrong one way and the first word of every answer is never learned; get
it wrong the other way and you're back to teaching it to write instructions.

The other half of the trick is the `<|endoftext|>` at the end. The base model
has no idea a document can *end* — it was trained on a stream. Supervising
that one token, in the same place, 50,000 times, is the entire reason the
tuned model stops talking.

## What actually happened

Two stages: Alpaca (52k machine-generated examples) then Dolly (15k
human-written ones), 18 minutes and 11 minutes on one 2080 Ti. Cheap.
Pretraining was 27 hours.

Those are the shipped numbers. Getting to them took nine checkpoints across
six runs, and the interesting part is that I nearly drew the wrong conclusion
four separate times.

### Wrong conclusion #1: "it works, look at it"

The tuned model answers questions and stops. Stop rate went from 30% to 98%,
average answer length from 179 tokens (i.e. running until I cut it off) to
about 52. Looping fell from 80% to ~15%. Every behavioural number improved
enormously, immediately, in the first 500 steps.

If I'd stopped there I'd have said the fine-tune was a success and moved on.

### Wrong conclusion #2: "so more of it must be better"

Three epochs of Alpaca gives you a model that *always* stops and *never*
loops. Perfect behaviour scores. It's also, measured on held-out data,
**worse than the base model it started from** — 3.0844 against 3.0580.

It has memorized what an Alpaca answer looks like. It emits a confident short
paragraph regardless of the question:

> **Who is Luke Skywalker's parents?**
> Luke Skywalker's parents are Luke Skywalker and Sarah.

Ten tokens, fluent, stops cleanly, total nonsense, delivered with complete
confidence. Every metric that *looks* like it measures instruction-following
says this is the best checkpoint I have. Held-out loss says it's the worst.

The lesson I want to keep: **the metrics that are easy to compute for this
task are the ones that measure format, and format is the thing that gets good
first and stops meaning anything fastest.**

### Wrong conclusion #3: "so keep the best checkpoint of a long run"

That is the textbook fix for #2, and it is what I did: the three-epoch run's
best checkpoint is step 1,500, which is one epoch, so one epoch it is.

Then I ran stage 1 again as a 1,600-step schedule that actually *finishes*,
and it beat that checkpoint on both held-out sets at once — 2.0512 against
2.0568 on Alpaca, 2.9145 against 2.9506 on Dolly — for a third of the GPU
time. Nearly the same steps over nearly the same data; the difference is that
step 1,500 of the long run sits mid-cosine at lr ~1.7e-5 and never gets the
annealing that does the last of the work.

The bit I didn't expect: it compounds. Running the identical 940-step stage 2
off the new checkpoint instead of the old one ends 0.028 nats better, on a
stage where nothing else changed. A better starting point stays better through
everything built on top of it, which is an argument for spending effort on
stage 1 rather than tuning stage 2 harder.

### Wrong conclusion #4: "stage two improved it"

Dolly's held-out loss improves with every epoch of the Dolly stage. Great.

Then I scored the same checkpoints on *Alpaca's* held-out split, and every
epoch that improved Dolly degraded Alpaca by almost exactly the same amount.
One epoch to three: −0.026 on Dolly, +0.022 on Alpaca. A one-for-one trade.

Stage two was not making the model better. It was moving where the model was
good. You cannot see that with one held-out set, and I only had one until I
went looking.

## The bug this shook out

The Alpaca run finished at step 4,875, logged a final training loss, ran a
final validation, printed both — and never saved. `checkpoint_interval` was
500 and 4,875 isn't a multiple of it, so the newest file on disk was step
4,500. The weights behind the final numbers simply didn't exist anywhere.

Logging and validation both already special-cased "is this the last step".
Saving didn't. It does now, with a test that asserts both directions (a run
ending off the interval gets a final file; one ending on it doesn't get a
duplicate). The very next run — 470 steps against a 150-step interval —
saved a `step_470.pt` it would otherwise have thrown away.

Cheap fix. The annoying part is that the damage isn't retroactive: the Alpaca
stage's actual final weights are gone for good.

## What it did not do

It taught the model to answer. It did not teach it to know anything.

> **What are brambles?**
> Brambles, also known as plasticizers, are chemical compounds that are added
> to a mixture of non-cemented polymers. They are also an essential part of a
> good manufacturing process.

Brambles are thorny shrubs. Right length, right register, right confident
encyclopaedic cadence, entirely invented — exactly the failure the base model
had, wearing a better suit.

Which makes sense. The whole fine-tune is about 5 million supervised tokens
on top of 2.46 billion pretraining tokens. Facts live in the pretrained
weights. Fine-tuning at this scale rearranges how they come out; it cannot
add any.

## If I do this again

- **Two held-out sets, from the start.** One tells you the model improved.
  Two tell you whether it improved or just moved.
- **Never select a checkpoint on behaviour metrics.** Stop rate and loop rate
  are diagnostics, not objectives. They peak on the checkpoint you least want.
- **Pack the sequences.** Every example is padded to 512 tokens, so ~78% of
  each step is padding and another ~11% is masked prompt. About 89% of the
  compute in both stages produced no gradient. That's a 4-5x speedup sitting
  untouched.
- **A finished short schedule beats an unfinished long one.** Early stopping
  hands you a checkpoint that never got its learning-rate decay. If the sweep
  says one epoch, run a one-epoch schedule, don't stop a three-epoch one.
- **Write the wrong prediction down before running the thing.** I predicted
  the Dolly stage would overfit in one epoch, wrote out the reasoning in the
  config, and was wrong. Having the argument on paper next to the curve that
  refuted it is the most useful artifact of the whole exercise.
