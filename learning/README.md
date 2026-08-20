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

---

*These notes are part of the repo. They are deliberately informal — the README
documents what the project **is**, this folder records what it **took**.*
