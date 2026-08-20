# 6. Packing the Window

File 5 ended with a list of things I'd do differently, and the third bullet
was "pack the sequences — that's a 4-5x speedup sitting untouched." This is
what happened when I went and did it. The speedup was real and arrived exactly
as predicted, and it is the least interesting thing in this file.

## The waste, one more time

Every instruction example got padded out to its own 512-token window. Alpaca
examples average 113 tokens, of which 58 are the response — the only part that
produces gradient. So each optimizer step was roughly:

```
[ 58 supervised | 55 masked prompt |            399 padding             ]
   11%               11%                          78%
```

The GPU does not know the padding is padding. A forward and backward pass
costs what the tensor shape costs, so ~89% of both fine-tuning stages was real
GPU time spent on nothing.

## The fix, and the two things holding it up

Lay whole examples end to end until the next one doesn't fit. That part is
easy. The part that makes it *correct* is two extra tensors:

- **`segment_ids`** — which example each position belongs to. Attention gets a
  block-diagonal mask on top of the causal one, so example 2 cannot see
  example 1. Without it, the model trains on context that will never exist at
  inference time.
- **`position_ids`** — position within its own example, restarting at 0. RoPE
  rotates by absolute position, so without this, every example after the first
  is rotated to positions the model only ever otherwise sees mid-document.

Here is the part I want to remember: **both of those bugs still train.** Leave
out the mask and the loss falls. Leave out the positions and the loss falls.
You get a believable curve, a checkpoint, and a model that is quietly wrong in
a way no metric on the dashboard reports.

So the test doesn't check the mask exists. It runs a real model over a packed
window, runs each of those examples through the same model alone, and demands
the logits match — plus a negative control that asserts they *stop* matching
when the mask is removed. If I'd tested "does it produce the right shapes" I'd
have shipped either bug happily.

There's a subtler one in the same family: gradient checkpointing recomputes
the forward pass, and it passes arguments through `torch.utils.checkpoint`. If
the packing arguments didn't survive that hop, the recomputed backward would
use plain causal attention while the original forward used the block-diagonal
mask. Same shape, same convergence, wrong gradients. That's a test now too.

## The number

Measured on a real step, not estimated:

| | ms/step | supervised tokens/step | supervised tok/s |
|---|---:|---:|---:|
| unpacked | 160 | 472 | 2,945 |
| packed | 163 | 2,111 | 12,953 |

4.40x for 1.02x the per-step cost. Alpaca's 50,868 examples pack into 11,220
windows at 98.8% fill. One epoch of stage 1 went from 18 minutes to 4.5.

The 3ms is the block-diagonal mask, which is bigger than the plain causal one
— `(batch, 1, seq, seq)` instead of a single shared `(seq, seq)` — and has to
be built and broadcast against every head. I benchmarked it rather than
assuming, because "fewer windows" is arithmetic but "the bigger mask is free"
is a claim about hardware. It turned out to be nearly free. It didn't have to.

## Then the actual lesson

I flipped packing on with the existing config and the model got **worse**.

Not slower — worse. 2.0912 held-out against the unpacked 2.0745, same data,
same single epoch, 4.4x the throughput. It took me an embarrassing minute to
see why: a packed step contains 4.5x the supervised tokens, so an epoch is 350
steps instead of 1,600, and the learning rate that was tuned for the old step
is now attached to a completely different one. I hadn't changed the schedule.
Packing changed it for me, silently, by changing what a step *is*.

Swept properly, packed keeps improving all the way to 1.5e-4 where unpacked
peaked at 3e-5 — about 5x apart, against a 4.53x batch ratio. That's linear
scaling. I'd have guessed square-root (it's the rule everyone quotes) and
square-root would have stopped me at 6e-5, less than halfway to where the
curve actually went.

I also ran the unpacked sweep as a control, because the boring explanation for
"packing helped" is "your baseline was badly tuned and anything would have
helped." It wasn't: unpacked peaks at 3e-5 and has already turned by 6e-5, so
the 2e-5 the stage shipped was about right for the batch it had. Running the
control cost an hour and is the only reason I can say that.

## And then the trap, for the third time

The packed sweep's held-out loss falls monotonically to 1.5e-4. So use that
one.

No. I ran the identical stage 2 off three stage-1 checkpoints — 3.0e-5,
9.0e-5, and the old unpacked one — and the final models came out in exactly
the reverse of the stage-1 Alpaca ranking. The best stage 1 by that metric
made the worst final model. (I never ran stage 2 from 1.5e-4, which scores
best of all at stage 1: by then it was already the worst checkpoint I had on
Dolly, which was the tell.)

Past about 3e-5, the extra learning rate stops teaching instruction-following
and starts driving the model deep into Alpaca's specific distribution — which
Alpaca's own held-out split structurally cannot detect, because it's drawn
from the same distribution.

That is the third time this project has taught me the same thing wearing a
different hat:

1. The behavioural metrics (stop rate, loop rate) peak on the checkpoint you
   least want.
2. The best checkpoint of a long run loses to a short run that finished.
3. Stage 1's own held-out loss picks the wrong stage 1 for the pipeline it
   feeds.

Every single time, the fix was the same: score against something the stage was
not trained on. I now think of "held-out" as a relative term. Held out *from
what* is the whole question.

## What I shipped, and the bit I couldn't resolve

Both packed pipelines beat the unpacked one. Between 3.0e-5 and 9.0e-5 the
mean of the two held-out sets differs by 0.0008, which is noise, and running
each through stage 2 didn't break the tie so much as show me what it was made
of: 9.0e-5's entire advantage on the mean is Alpaca loss that stage 2 didn't
wash out. That's either a better-preserved model, or just one stage 2 moved
less — which is file 5's "stage 2 relocates the model" effect, not a gain.

Both readings fit the numbers. I could not find a measurement that separates
them, so I shipped both configs with their numbers instead of picking one and
writing a confident sentence about it. `sft_alpaca_packed.yaml` at 3.0e-5 is
the default because it gives the best Dolly held-out loss this repo has
produced, and Dolly is the distribution stage 2 exists to fit.

## The half-hour I lost to a default

Two eval reports refused to reproduce numbers from the earlier ones. Same
checkpoints, same script, same data, different loss. The cause was
`--loss-batches`, which defaults to 40, and the published tables were run at
34. The flag doesn't change how loss is computed — it changes *which subset*
"held-out loss" means, and at 40 the base model scores 3.0653 on Dolly instead
of 3.0580.

Nothing was wrong. Both numbers were correct measurements of slightly
different things, with the same name. Now every published number says which,
and I'd rather a flag like that had no default at all.

## If I do this again

- **A throughput change is a schedule change.** Anything that alters what one
  optimizer step contains — packing, batch size, accumulation — invalidates
  the learning rate that was tuned against the old step. There is no such
  thing as "just turning on packing".
- **Sweep the control too.** "My change helped" and "my baseline was bad" look
  identical from one column of numbers.
- **Test the equivalence, not the shape.** For any optimization that claims to
  be mathematically identical, the test is old-path output == new-path output
  on a real model, with a negative control proving the test can fail.
- **Ask what a held-out set is held out from.** Three times now, the metric
  nearest the training distribution has picked the wrong checkpoint.
