# Learning Notes — The 124M Training Run

A plain-English record of what happened when AshuGPT went from "a transformer
I wrote" to "a transformer I actually pretrained." Written for my future self,
so nothing here assumes you remember the details.

Read in order:

1. **[Where we started](01-where-we-started.md)** — what existed before, and
   the one honest gap in it.
2. **[What we did and why](02-what-we-did-and-why.md)** — the plan, and the
   reasoning behind each piece we had to build.
3. **[What went wrong](03-challenges.md)** — seven real problems, and how each
   one was actually fixed. The most useful file here.
4. **[How it turned out](04-results.md)** — the final numbers, what they mean,
   and what's still left to do.
5. **[Teaching it to answer](05-instruction-tuning.md)** — the instruction
   fine-tune that came after, and the four wrong conclusions I nearly drew
   from it.
6. **[Packing the window](06-packing-the-window.md)** — recovering the 89% of
   fine-tuning compute that was padding, and discovering that a throughput
   change is a schedule change.
7. **[Teaching it to prefer](07-preference-tuning.md)** — the first stage that
   is shown a *bad* answer, and the four ways its own headline metric
   flattered it.

## The 30-second version

The repo could *build* a 124M-parameter model but had only ever *trained* toy
ones (~14M) on tiny corpora. So we ran the real thing: 124M parameters, 2.46
billion tokens of FineWeb-Edu, on one RTX 2080 Ti, for about 27 hours of
compute spread across two days.

It worked. Loss went from 9.51 to 3.19, validation perplexity landed at
**23.53**, and the run finished all 20,000 steps and exited cleanly.

Getting there took five failed launches, one OOM crash, one reboot that killed
everything, and a status reporter that confidently published a wrong ETA. All
of that is written down in file 3, because that's the part worth remembering.

Afterwards it was instruction-tuned on Alpaca and Dolly — 29 minutes of GPU
against pretraining's 27 hours — which taught it to answer questions and stop,
and taught me that the metrics which look like they measure
instruction-following peak on the checkpoint you least want. That's file 5.

Then I packed the fine-tuning windows, because ~89% of both stages was padding.
It came out 4.4x faster and, once the learning rate was re-tuned for a step
that now means something different, better as well. That's file 6.

Then I gave it preferences — pairs of answers with a human's ranking, trained
with DPO. The objective learned what it was asked to learn, and almost none of
that was what I wanted: measured without reference to the model it started
from, its preference for the better answer moved half a percentage point, and
what it was really ranking by was answer length. That's file 7, and it is the
fourth time in this project that the metric closest to the training objective
pointed the wrong way.

---

*These notes are part of the repo. They are deliberately informal — the README
documents what the project **is**, this folder records what it **took**.*
