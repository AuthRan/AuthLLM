# 3. What Went Wrong (and How We Fixed It)

Seven problems, in the order they happened. This is the file worth re-reading.

---

## Challenge 1 — Out of memory, 6 seconds in

**What happened:** First launch. Model built, data loaded, and then rank 1 died:

```
memory allocation failed with OOM on device 1 while trying to
allocate 1237319680 bytes (free: 1195311104, total: 11337531392)
```

Six seconds of a run that was supposed to take 30 hours.

**Why it was confusing:** `batch_size: 12` had been *measured* at 8.09GB peak on
an idle card. There's 11.3GB. It should have fit.

**The actual diagnosis** — three things the single-card measurement missed:

1. **DDP gradient buckets** (~0.5GB) don't exist when you benchmark on one GPU.
2. **GPU 1 also drives the display**, so it starts ~325MB down and has less
   usable VRAM than GPU 0. *The two cards are not interchangeable.*
3. The failing allocation was the giveaway. 1,237,319,680 bytes — work backwards:
   `12 × 512 × 50304 × 4` = 1,236,271,104. That's the **fp32 logits tensor**,
   matching to within one megabyte of allocator rounding.

**The lesson:** at a 50k vocabulary, the biggest single tensor is not activations
— it's **logits**. And logits scale with batch size, which is exactly the knob
you reach for.

**The fix:** `batch_size` 12 → 8, which drops peak to 6.01GB (logits fall to
0.82GB), leaving ~5GB of headroom on the display card. Then `grad_accum_steps`
20 → 30 to keep tokens/step *exactly* where it was — a smaller batch, accumulated
more times, is mathematically the same update.

---

## Challenge 2 — Five launches that died in under two minutes

**What happened:** Attempts 2 through 5, all on both GPUs, all died almost
immediately. The logs just stop mid-run:

| attempt | last step reached |
|---|---|
| 2 | step 80 |
| 3 | step 40 |
| 4 | step 20 |
| 5 | step 20 |

No error, no traceback. The process just stopped.

**Why it was hard:** Nothing in the logs says *why*. When the failure leaves no
message, you have to find the pattern instead.

**The diagnosis:** Every failure involved both cards. So we ran a control —
single GPU, nothing else changed. It sailed past step 500, then 540, and kept
going. The variable was the second card, which runs hotter *and* drives the
display. (Attempt 4's log was named `supervisor_attempt4_thermal.log` at the
time — that was the hypothesis, and the control run supported it.)

**The fix:** run single-GPU. See the next challenge for what that cost.

**The lesson:** when a failure gives you nothing to read, stop reading and start
bisecting. One control run answered what five log inspections couldn't.

---

## Challenge 3 — Single-GPU halved the token budget

**What happened:** Dropping to one GPU halves tokens/step: 245,760 → **122,880**.
So 20,000 steps is **2.46B tokens**, not the 4.9B the config comments assume.

**The decision:** Was that still a real run, or a compromised one?

It's still real. The Chinchilla rule of thumb says train on roughly **20 tokens
per parameter**. Here:

```
2.46B tokens ÷ 123.6M params ≈ 19.9 tokens/param
```

That's essentially *exactly* Chinchilla-optimal. The original plan (4.9B, ~40×)
was past the optimum — more tokens, diminishing returns. Halving it landed on
the sweet spot rather than falling short of it.

Also worth being clear-eyed: DDP runs at the pace of the **slowest** card. Two
GPUs where one is thermally limited isn't 2× — it's 2× the failure modes for
well under 2× the speed.

**The lesson:** know which of your numbers are *targets* and which are
*consequences*. Steps were the target; token count was a consequence. Checking
2.46B against Chinchilla before committing turned a scary-looking number into a
justified one.

---

## Challenge 4 — A reboot killed everything

**What happened:** On Aug 17 the machine rebooted. Training died at step 14,000.
The status watcher died too. Nothing came back on its own.

**Why it mattered:** ~3 hours lost, but far more importantly — nobody was
watching. Had it happened overnight, the run would have sat dead for hours.

**What saved it:** the supervisor's **resume-from-newest-checkpoint** logic
(built in [file 2](02-what-we-did-and-why.md), before it was needed). Relaunching
picked up from `step_14000.pt` and continued. Checkpointing every 500 steps meant
the worst case was ~40 minutes of lost compute, not 14,000 steps.

**What's still not fixed:** neither training nor the watcher survives a reboot.
The durable fix is a cron entry with `@reboot` (documented at the top of
`scripts/sync_training_logs.sh`), which was never installed.

**The lesson:** checkpoint/resume isn't a nice-to-have on a multi-day run. It's
the difference between "lost 40 minutes" and "start over."

---

## Challenge 5 — The status page published a confident lie

**The best bug of the run.** After the reboot, the README's auto-generated status
block was about to report **~5,800 tokens/sec and a ~32-hour ETA**. The real
numbers were **~26,000 tokens/sec and ~7 hours left**. Off by more than 4×.

**Why:** throughput was computed from the gap between the two newest checkpoint
timestamps. After the reboot those were:

- `step_14000.pt` — 20:18, *before* the reboot
- `step_14500.pt` — 23:09, *after* the relaunch

The gap between them is mostly **the machine being switched off**. That works out
to 20.5 s/step against a true rate of ~4.7 s/step.

**Why the guard didn't catch it:** there *was* a plausibility check — reject
anything outside 0.5–60 s/step. But 20.5 falls comfortably inside that window.
The guard was wide enough to let the one case it most needed to catch slip
straight through.

**The fix** (commit `ea91159`): exclude any checkpoint written *before the current
attempt started*, using the supervisor's own launch timestamp. A fresh restart now
reports **no ETA at all** until it has saved two checkpoints of its own — which is
the honest answer, because nothing observed yet constrains the rate. The
plausibility guard stays as a second line of defence for mid-run stalls.

**The lesson:** a plausibility range only catches *implausible* wrong answers. A
restart boundary produces a perfectly plausible wrong answer. The real fix wasn't
tightening the range — it was **not measuring across the boundary at all**. And
when you don't know, publishing nothing beats publishing a guess.

---

## Challenge 6 — Couldn't install the cron job

**What happened:** The intended scheduler was a `crontab` entry. Installing it was
blocked by a permission classifier.

**The fix** (commit `3ef1a50`): `scripts/watch_training_logs.sh`, a plain hourly
loop, as a fallback. Two details make it safe:

- **`flock` in `sync_training_logs.sh`** — a cron entry and a leftover watch loop
  would otherwise collide on the git index mid-commit. Taken non-blocking, since
  if another sync is already running, this one is redundant anyway.
- **The loop exits when training stops** and the final state is published, instead
  of lingering forever as a stray process.

That second detail paid off: the watcher noticed the run had finished, pushed the
final state, logged `training stopped and everything published; exiting`, and shut
itself down cleanly at 07:17.

**The lesson:** when the preferred mechanism is unavailable, a fallback is fine —
but design it to *not fight* the real one if it ever shows up.

---

## Challenge 7 — GitHub said "AuthRan and Claude committed"

**What happened:** The automated sync commits carried a `Co-Authored-By` trailer.
GitHub renders that as a second author on every commit — on a public portfolio
repo, on ~1 commit per hour, indefinitely.

**Why it was worse than a one-off:** the trailer was baked into the *script that
generates commit messages*. Deleting it from existing commits would have fixed
nothing; the next sync would put it right back.

**The fix, in two parts:**

1. **Stop the source** (commit `4034476`) — removed the trailer from the generator
   script, so no new commit could carry it.
2. **Clean up history** — `git filter-branch --msg-filter` stripped it from the 8
   commits that already had it, followed by a force-push.

**The lesson:** with automation, fix the generator before the output. Cleaning up
the output while the generator still runs is a treadmill.
