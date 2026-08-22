# 7. Teaching It to Prefer

Every training stage before this one works by imitation. Pretraining copies
FineWeb-Edu, the fine-tunes copy Alpaca and Dolly, and cross-entropy carries
exactly one instruction: *be more like this*. There is no way to express "this
answer is better than that one", because no training example ever contains two
answers.

That is the gap DPO fills, and the reason it is the last thing in this repo
rather than the first is that it is the first stage whose data a human had to
*rank* rather than write.

## The trick, in one paragraph

The textbook route is RLHF: train a reward model on the rankings, then push
the policy up that reward with PPO. Four models, a sampling loop, and a
reinforcement-learning algorithm with a reputation for being difficult to keep
stable. DPO's paper points out that the optimal policy for that objective has
a closed form, and if you substitute it back in, the reward model cancels. What
is left is a classification loss over pairs — is the chosen answer more likely
than the rejected one, relative to where training started — which trains like
any other supervised objective. Two forward passes and a backward.

The first time I read that, it sounded like a free lunch. It isn't quite: what
you lose is the ability to score answers the model generates *now*, because
there is no reward model to ask. DPO only ever sees the pairs in the dataset.

## The frozen reference is the whole thing

`log pi(y) - log ref(y)` — how much more likely the policy has made answer `y`
than a frozen copy of the model the run started from — is what DPO calls an
implicit reward. It is never fitted. It is measured, by running both models.

Take the reference away and the objective becomes "make chosen more likely
than rejected", which is satisfied perfectly by a model that assigns both of
them almost zero probability. Nothing in the ranking forbids destroying the
model on the way there. Anchoring both terms to a fixed starting point makes
the loss about *changes*, so drifting far costs something on both sides. It is
the same job KL regularization does in RLHF, arrived at by algebra instead of
by adding a penalty term.

Which means the single most important line in my implementation is the one
that keeps the reference frozen — `requires_grad_(False)` plus an override of
`train()` so nothing puts it back in training mode. A run with a leaking
reference does not crash. It shows a falling loss and a rising accuracy
against a baseline that is quietly moving with it.

## The bug I nearly shipped, which was a metric

`accuracy` — the fraction of pairs whose chosen answer the implicit reward
ranks first — is the headline number of a DPO run. I wrote it as
`(chosen_reward > rejected_reward).mean()`, which is the obvious thing, and
the docstring I wrote above it said it "starts at ~50%".

Both statements are true separately and false together. At step 0 the policy
*is* the reference, so every reward is exactly zero, every comparison is a
tie, and a strict `>` scores every tie as wrong: 0%, not 50%. The first
evaluation of every run would have looked like a catastrophe, and I would have
gone looking for the bug in the loss.

Ties count half now. It is three characters of arithmetic and it is the
difference between a metric that reports "undecided" and one that reports
"wrong".

## What the data actually contains

I used Anthropic's HH-RLHF, because it is the preference dataset everyone
starts with. It is worth being precise about what its labels mean, because I
was not, at first.

It is two datasets in one file. The *helpfulness* half ranks two attempts at
being useful. The *harmlessness* half is red-teaming: the prompt is an attempt
to get the model to help with something it should not, and the "chosen" answer
is the one that declines. The very first pair in my prepared file is a
question about shoplifting where the chosen answer is a deflection and the
rejected answer is genuinely informative.

So "preferred" here does not mean "better answer". It means "the answer a
human labeller picked, for reasons that include both helpfulness and refusal",
and a model trained on the mixture learns both at once. That is worth knowing
before reading any sample from the tuned model as evidence about quality.

The other thing worth measuring before the run: chosen answers in this set
average 80 tokens against the rejected answers' 72. Summed log-probabilities
are all negative, so a longer answer scores lower simply for being longer —
which means "prefer the shorter one" is a real strategy that would show up as
progress. That is why the evaluation reports per-token accuracy alongside the
raw number. If the two ever diverge badly, the model has found the shortcut.

## The sweep, and the part where I stopped trusting the numbers

Three learning rates, 400 steps each, complete cosine cycles so the endpoints
compare. Then every checkpoint scored on HH-RLHF's own test split — different
conversations, nothing the runs had seen.

Ranked by anything preference-shaped, the order is unambiguous: 2.0e-5 first,
then 5.0e-6, then 1.0e-6. Highest DPO accuracy, biggest margin, best held-out
DPO loss, best per-token ranking. If I had shipped on that I would have shipped
2.0e-5 and written a paragraph about how more learning rate helped.

Then I ran the same instruction-following eval that section 10.4 uses, because
this repo has now been burned three separate times by a metric that sits close
to the training objective. The order reverses completely. 2.0e-5 is the only
checkpoint in the sweep that is *worse than the model it started from* on every
behavioural measure: it stops on its own less often than the untuned baseline
(88% against 92%), its answers grow from 62 tokens to 103, and its loop rate
doubles from 20% to 40%. 1.0e-6, the run that looked weakest on every
preference metric, is the only one that improves the model at all.

I want to be honest that I did not predict this. I ran the behavioural eval out
of habit, and it changed the answer.

## The number that should have warned me

It was in the sweep table the whole time, and it is the reason I now print the
two implicit rewards separately instead of only their difference.

```
lr 2.0e-5:  chosen -0.6595   rejected -0.8270   margin +0.1674
```

The margin is positive, so the loss is happy. But both terms are *negative*.
At beta = 0.1 that says the policy has made the answers a human preferred about
700x less likely than the model it started from, and the rejected ones about
3,700x less likely. It is not learning to prefer good answers. It is retreating
from both and retreating from one faster, and a metric built out of the
difference cannot see the retreat at all.

That is the thing I would tell myself if I were starting this again: **in a DPO
run, watch the two rewards, not the margin.** The margin is what the loss
optimizes and it goes up no matter which of the two very different things is
happening underneath.

## What it did not learn, which is most of it

The reference-free number is the one that deflates the whole exercise. Ask the
model, with no reference involved, whether it assigns more probability to the
chosen answer than the rejected one, and the SFT model gets 46.3%. After DPO at
the most aggressive setting in the sweep, 46.8%.

Half a point. The headline DPO accuracy over the same checkpoints moved ten.

Both are true. DPO optimizes a *difference from where you started*, and that
difference improved exactly as advertised — it never claimed the model would
end up preferring good answers in absolute terms. I just hadn't internalized
how far apart those two statements are until I had numbers for both.

And when I split raw accuracy by which answer is longer, the picture gets
worse and much clearer:

- chosen answer **shorter** than rejected: ranked first **92.8%** of the time
- chosen answer **longer**: **8.3%**

That is not a preference model. That is a ruler. Summed log-probabilities are
all negative, so every extra token costs another 2-3 nats, and an answer eight
tokens longer starts about 20 nats behind — far more than any plausible
difference in content. HH-RLHF's chosen answers average 80 tokens against 72,
so the shortcut is there to take, and the model takes it every single time.

DPO moved those two numbers to 92.7% and 9.1%. It did not touch the length
prior. What it did move is per-token accuracy, 54.3% to 56.6%, which is real
ranking-by-content and is a small fraction of what the headline suggested.

## The epoch I did not ship

The sweep picked 1.0e-6, so I ran it properly: a full epoch, 1,495 steps, one
complete cosine cycle. The held-out DPO loss fell the entire way and was still
falling at the last eval, which is the shape that always makes me want to train
longer.

Every other number says the extra 1,095 steps did nothing. 1.3 points of the
metric being optimized. Half a point of ranking-by-content, all of it arriving
by step 598. And a slow drift the wrong way on behaviour — answers lengthening
62 → 70 → 77 tokens, loop rate finishing at 25% against the untuned model's
20%.

So the 400-step run ships and the epoch is documented beside it. I want to be
careful about how hard I lean on that: loop rate comes off 40 generations, so
18% against 25% is seven of them against ten. The honest version is not "the
long run is worse", it is "the long run costs 3.7x and shows nothing for it
outside its own objective", which is enough.

The thing that kept climbing through those extra steps was the margin, +0.05 to
+0.09. Which, as above, is not skill — it is distance from the reference, and
the two rewards say the distance is made of retreating from both answers
unevenly.

## Three things I would do differently

**Pick data whose chosen answers aren't systematically longer.** The 80-vs-73
gap is worth more nats than any content signal in the set, and everything
downstream gets measured through it. I wired up UltraFeedback in the prepare
script as an alternative and did not run it.

**~~Try the length-normalized variant.~~** I ran it after writing this, and it
taught me something I did not expect — see the section below.

**Raise beta before lowering the learning rate.** Both rewards going negative
is a KL problem and beta is the knob for it. I swept the learning rate because
that is the habit three previous stages built. beta = 0.2 is the experiment I
should have run.

## The variant that failed, and why that was the useful part

So I ran it. Averaging each sequence's log-probability instead of summing it
takes the length term out of the objective, which is precisely the thing my
own evaluation said was dominating. Two betas, because normalizing shrinks the
margins ~80x and 0.1 would have been an ~80x weaker pull: 8.0 to match, 1.0 to
undershoot deliberately.

Raw accuracy went from 46.3% to 46.5%. The split I had used to diagnose the
whole problem — chosen-first 92.8% of the time when it is shorter, 8.3% when
it is longer — came back 93.0% and 8.4%. Training with the length term
removed made the length bias very slightly *worse*.

I stared at that for a while before it landed, and when it did it was
embarrassing in a useful way. Raw ranking accuracy scores a model by the
summed log-probability of a sequence. That sum is negative and it grows with
length no matter what the weights are. I had removed the length term from the
objective and then gone on measuring with a ruler that still charged 2-3 nats
a token. No training run, under any objective, was ever going to move that
column — the shortcut was in my measurement at least as much as in the model.

Which means the sentence I wrote three sections earlier, "it did not touch the
length prior", was doing more work than it had earned. The length prior is
partly just what summed log-probabilities *are*. The honest version
is that the model has a length prior, my metric has one too, and I had been
reading the second one as evidence about the first.

The column that already divides length out is per-token accuracy, and there
the variant did show up: 55.9% at beta 1.0 against standard DPO's 55.2%, off
54.3% before any preference tuning. About 1.8x the movement, which is a real
effect and a small one.

And then the split I should have predicted by now. beta 8.0 wins the trained
objective and is the worst of the three tuned checkpoints at ranking by
content. beta 1.0 is the reverse, and it is also the one that damages the
model — 88% stop rate against 92%, answers 39% longer. beta 8.0 costs 0.0015
nats of Dolly loss, which is nothing, and is the only checkpoint in the whole
stage whose answers got *shorter*. That is beta holding the policy near the
reference, doing the job I had guessed it would do, while length normalization
did not do the job I built it for.

Both rewards still go negative under normalization at both betas, so the KL
retreat is untouched too. Nothing from this experiment ships. It cost half an
hour of GPU and it corrected a claim in my own writeup, which is a better
trade than it sounds.

## The one-sentence version

DPO worked exactly as specified, and "exactly as specified" turned out to mean
something much narrower than what I wanted from it — which I would not know if
I had only looked at the loss, the accuracy, or the margin, because all three
went the right way the whole time.
