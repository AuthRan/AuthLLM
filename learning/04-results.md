# 4. How It Turned Out

## The finish

The run ended on its own terms at **06:39 IST on 2026-08-18**:

```
step 20000 | train_loss 3.1920 | lr 6.00e-05
step 20000 | val_loss 3.1583 | val_ppl 23.53
Saved checkpoint to checkpoints/medium/step_20000.pt
[supervisor] training finished successfully
```

No crash, no manual stop. It hit the target step count, ran a final validation,
saved, and exited.

## Final numbers

| | |
|---|---|
| Steps | 20,000 / 20,000 |
| Final training loss | 3.1920 |
| Final validation loss | 3.1583 |
| **Validation perplexity** | **23.53** |
| Tokens seen | 2.46B (122,880/step) |
| Throughput | 4.91 s/step (~25,000 tokens/s) |
| Hardware | 1× RTX 2080 Ti (11GB), fp16 + GradScaler |
| Parameters | 123,587,328 |

## What those numbers mean

**Loss** is how surprised the model is by the correct next token. At random
initialization with a 50,304-token vocabulary, loss would be
`ln(50304) ≈ 10.83` — total ignorance, every token equally likely. The first
logged step was already at 9.51, and it finished at **3.19**.

**Perplexity 23.53** is the loss exponentiated, and it has a nicer
interpretation: on average the model is about as uncertain as if it were
choosing between roughly **23 or 24 equally likely options** — instead of 50,304.
That's the whole result in one number.

## The learning curve

Validation loss, measured every 500 steps, went down monotonically the whole way:

| step | val loss | perplexity |
|---|---|---|
| 6,000 | 3.437 | 31.08 |
| 8,000 | 3.363 | 28.87 |
| 10,000 | 3.310 | 27.39 |
| 12,000 | 3.265 | 26.18 |
| 14,000 | 3.224 | 25.13 |
| 16,000 | 3.191 | 24.30 |
| 18,000 | 3.168 | 23.77 |
| **20,000** | **3.158** | **23.53** |

Two things to read here:

1. **No divergence, no loss spikes, no instability** — across 20,000 steps, a
   reboot, and a mid-run restart. For fp16 training (which *can* blow up when
   gradient scaling goes wrong) that's the real proof the training loop is sound.
2. **The gains flattened at the end** — only −0.010 over the last 2,000 steps,
   versus −0.045 between steps 10k and 12k. That's expected: the cosine learning
   rate schedule had decayed to its floor of 6e-5. The run stopped at a sensible
   place rather than being cut off mid-descent.

## Timeline

| when | what |
|---|---|
| Aug 16, ~21:00 | Data ready: 5,000,186,213 tokens. First launch → OOM |
| Aug 16, 21:20–22:47 | Attempts 2–5, all dying within ~2 minutes |
| Aug 16, 22:55 | Single-GPU control run — **stable**, this is the real run |
| Aug 17, ~20:18 | Reboot kills everything at step 14,000 |
| Aug 17, 23:09 | Relaunched, resumed from checkpoint |
| Aug 18, 06:39 | **Finished** at step 20,000 |
| Aug 18, 07:17 | Watcher published final state and exited cleanly |

Roughly **27 hours of actual compute**, spread across two days.

## What this proves

The from-scratch code — the model, the training loop, the mixed-precision
handling, the checkpointing, the data pipeline — works at real scale, on real
data, for a day and a half straight, including surviving a machine reboot.

That's the gap from [file 1](01-where-we-started.md), closed. The README now
says `medium` was **trained to completion**, and the status block backs it with
numbers generated from the run's own artifacts.

## What's still left

- **Nothing has been run against the finished checkpoint yet.** `scripts/evaluate.py`
  and `scripts/export_inference.py` exist but haven't been pointed at
  `step_20000.pt`. There's a trained 124M model that nobody has generated a single
  sample from.
- **The live demo still serves the old `small` model** (~14M, TinyStories,
  perplexity ≈ 11). Swapping in the 124M model would make the demo match the
  headline.
- **The branch was never merged.** `124-million-training` is ~15 commits ahead of
  `main`, and the run it was created for is done.
- **56GB of checkpoints** — 40 files, one every 500 steps. Only `step_20000.pt`
  matters now; pruning the rest reclaims ~55GB. (They're gitignored, so the repo
  isn't affected.)
- **Reboot durability was never actually fixed** — see
  [challenge 4](03-challenges.md). Still needs the `@reboot` cron entry.

## The five lessons, condensed

1. **Benchmark on the machine you'll actually use.** bf16 is the "right" choice
   everywhere except Turing, where it's emulated and 7.4× slower than fp16.
2. **At a 50k vocab, logits are your biggest tensor** — not activations. And they
   scale with batch size.
3. **Your two GPUs are not interchangeable** if one drives the display.
4. **When there's no error message, bisect instead of reading.** One control run
   beat five log inspections.
5. **Publish nothing rather than a plausible guess.** The ETA bug passed its
   sanity check precisely because the wrong answer looked reasonable.
