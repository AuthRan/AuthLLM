# What the 124M Model Actually Writes

> **This page is about the base model.** It was later instruction-tuned on
> Alpaca and Dolly, which changed its behaviour a great deal and its knowledge
> not at all — [instruction-tuning.md](instruction-tuning.md) has that story,
> with held-out measurements rather than impressions.

The run finished. Perplexity 23.53. That number tells you almost nothing about
what the thing sounds like, so I sat down and generated a pile of text from the
step-20,000 checkpoint and read all of it.

Short version: it writes real English. Confident, well-formed, properly
punctuated English that is frequently, hilariously wrong about the world.

All the raw output is in [samples.md](samples.md). Nothing there is
cherry-picked. I ran ten prompts, kept all ten, and I've quoted the bad ones
here too because the bad ones are more interesting.

## First, the good news

Here's the model on early literacy, unprompted beyond five words:

> Children learn to read by taking time to sit down to a story and be silly so
> they do not feel bored. A fun way to boost your child's motivation to read is
> to tell them stories they are interested in... At home, try to read at least
> two books together per day. Reading to your child early will help your child
> develop a love for books, reading vocabulary, comprehension abilities, and
> literacy.

I'd believe a school newsletter wrote that. It's not just grammatical, it holds
a topic across five sentences, keeps a consistent voice, and the advice is
actually sound. Nothing in there is false.

And the volcano one is genuinely close to correct:

> A volcano erupts when the earth's mantle is subjected to severe pressure.
> This pressure pushes it to the surface...

It goes off the rails a couple of sentences later (it invents a "lava plain"
and a "volcanic arch"), but the opening is the kind of thing you'd find in a
middle-school textbook.

So: subject-verb agreement, clause structure, paragraph breaks, topic
coherence, register. All learned from scratch, from random weights, in about
27 hours on one gaming GPU. That part worked.

## Now the part that made me laugh

Ask it about history and it produces something that reads like a student who
did none of the reading and is bluffing:

> The main causes of World War I were the collapse of the Ottoman Empire, the
> death of the so-called "Greater Germany" and the outbreak of World War II
> under the leadership of Adolf Hitler.

World War I was caused by World War II. Chef's kiss.

The Roman Empire one is somehow worse, and it's worse in an interesting way:

> The Roman Empire fell because the emperor Augustus... was forced to lose
> power to his rival Etruscans. However, the conflict was not a problem for the
> Roman Empire, but for the Roman Empire.

Look at that last clause. "Not a problem for the Roman Empire, but for the
Roman Empire." It has learned the *shape* of a contrast — "not X, but Y" — and
filled both slots with the same noun phrase because it's the one that's been
priming the whole passage. The grammar of contrast is there. The concept isn't.

Then DNA, which is my favourite:

> DNA is made up of three elements: the four nucleotides, the three chemical
> bases called carbon-12, nitrogen-14, and oxygen-13.

Three elements, which are four nucleotides, which are three bases. It can't
count. It's also fused two totally unrelated things it saw in training —
nucleotide names and isotope notation — because both show up in sciencey text
near the word "elements."

## What I think is actually going on

The pattern across every one of these is the same, and once you see it you
can't unsee it: **the model learned what educational writing looks like, not
what it says.**

Every sample has the right furniture. Topic sentence, then elaboration, then a
header, then a list. The photosynthesis one spontaneously emits "Types of
Photosynthesis" as a section heading. The fractions one produces a clean
bulleted list of teaching tips. The quadratic one lays out "Step 3: Solving the
Quadratic equation." That's the format of FineWeb-Edu, absorbed perfectly.

What's missing is any grounding underneath the format. It knows "photosynthesis"
travels with "sunlight," "carbon dioxide," "energy," and "chemical." It does not
know which way the arrow points, so it cheerfully says photosynthesis converts
CO2 *into water*.

This isn't a bug in the code and it isn't a sign the run went wrong. It's
exactly what 124M parameters buys you. Facts are expensive — they need capacity
to store and a lot more tokens to pin down. Syntax is cheap by comparison and
shows up early. At 2.46B tokens and this parameter count, syntax is what you
get. If the samples had been fluent *and* accurate I'd be suspicious that
something was leaking the validation set.

## The temperature thing is worth a look

I ran the same two prompts at three temperatures, and the difference is the
clearest window into the model's limits.

At **0.3** it collapses into a loop almost immediately:

> The Industrial Revolution was a period of rapid industrialization and
> industrialization in which the United States was the dominant economic power
> in the world. The Industrial Revolution was a period of rapid
> industrialization and industrialization in which the United States was the
> dominant economic power in the world. The Industrial Revolution was...

Verbatim repetition, three times, until it runs out of tokens. ("Rapid
industrialization and industrialization" is a nice touch on its own.)

At **0.8** it's the readable stuff quoted above. At **1.0** it stays coherent
but drifts further, and produced the single strangest output of the whole
session:

> The Industrial Revolution began in 1815 when the first steam-powered flour
> mill was completed in London, England.
>
> This section contains 4,234 words
>
> (approx. 11 pages at 300 words per page)

That's not the model failing. That's the model faithfully reproducing study-guide
website boilerplate, because that boilerplate is *in* FineWeb-Edu and it learned
it as legitimate document structure. It even invented a plausible word count.

The looping at low temperature is the honest signal here. It means the model's
probability distribution over the next token is shallow — there's a most-likely
continuation, and if you always take something near it, you fall into an
attractor and never leave. Sampling at 0.8 hides that by injecting enough noise
to keep it moving. Every small model does this. It's the thing that goes away
with scale, and it's a useful reminder that "the samples look decent" and "the
model is good" aren't the same claim.

## Unconditional generation

With no prompt at all — just the document-start token — it writes plausible web
pages out of nothing:

> The latest news from academia, regulators
> research labs and other things of interest
> Posted: Dec 30, 2010

That's a news-aggregator header, complete with a dateline, invented from
scratch. It learned that documents *begin* a certain way. Which, honestly, is a
slightly eerie thing to see fall out of gradient descent on a pile of web text.

## Speed

~90 tokens/sec on the 2080 Ti with the KV cache on. Fine for a demo. Confirms
the cached inference path works on a real model and not just the toy one.

## What I'd take away from this

The code works. That was the whole question this run existed to answer, and the
samples answer it better than perplexity does — you can't fake paragraph
structure and register with a broken attention implementation or a subtly wrong
loss.

The model is a base model in the truest sense. It continues text. It doesn't
answer questions, follow instructions, or know when to stop, because nothing
ever taught it to. If I want it to *respond* rather than *continue*, that's a
fine-tune, not more pretraining.

*(I did that next. It worked, in the narrow sense that it now answers and
stops — 30% of generations terminated on their own before, 100% after — and
did nothing whatsoever for the facts:*
[instruction-tuning.md](instruction-tuning.md)*.)*

And the facts aren't coming at this size. No amount of extra tokens fixes
"World War I was caused by Hitler" at 124M parameters. That's a capacity
problem, and the fix is a bigger model, which this hardware can't hold.

## Reproducing any of this

```bash
python scripts/sample.py \
    --checkpoint checkpoints/medium/step_20000.pt \
    --temperature 0.8 --top-k 50 --seed 1337 \
    "The process of photosynthesis"
```

The seed resets before each prompt, so you get the same text whether you run
one prompt or all ten.
