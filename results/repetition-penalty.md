# What a Repetition Penalty Bought

Every checkpoint in this project loops. Loop rate -- a repeated ten-token
window anywhere in a generation -- is 20% for the instruction-tuned model, 18%
after DPO, 30% after chat tuning ([§10](../README.md#10-instruction-tuning)),
and it is the most visible thing wrong with the samples. It is also the one
failure none of the training stages addressed, because none of them could:
the model is not repeating because it was trained badly, it is repeating
because nothing at sampling time can see that it already said this.

`temperature`, `top_k` and `top_p` all decide *which* tokens may be sampled at
one step, from that step's logits alone. None of them has any memory. The
filter that looks backwards is a repetition penalty, and this repo did not
have one.

## The implementation

`apply_repetition_penalty` in `ashugpt/inference/generate.py`, following
Keskar et al. 2019 (CTRL): divide the logit of every token already in the
context by the penalty if it is positive, and multiply it if it is negative.

The asymmetry is the whole trick. Dividing a negative logit would move it
*toward* zero and make an already-disfavoured token more likely -- a penalty
that rewards half its targets. Both branches have to move the score down.

The generation loop tracks the token ids separately from everything else it
carries, which is the one interesting mechanical detail: with a KV cache the
sequence deliberately is not kept around, because the context lives in the
cache as K/V and `next_input` is a single token after the first step. The
penalty is the only thing here that needs the ids themselves.

## The sweep

The instruction-tuned checkpoint (`sft_dolly_packed3e5/step_940.pt`), scored
by `scripts/eval_instruction_following.py` on the same held-out Dolly split,
same seed and same sampling settings as every other table in this repo -- only
the penalty changes.

| repetition penalty | stop rate | mean tokens | loop rate |
| --- | ---: | ---: | ---: |
| 1.0 (off, what shipped) | 92% | **62** | 20% |
| **1.1** | **98%** | 74 | 8% |
| 1.2 | 88% | 102 | **0%** |
| 1.5 | 48% | 154 | **0%** |

Held-out loss is 2.7444 at every row, unchanged, because loss is teacher-forced
and never samples. That is worth saying rather than omitting: the number this
project usually reaches for cannot see this change at all.

**1.1 is the only setting that improves both.** Stop rate 92% -> 98% and loop
rate 20% -> 8%, for 12 more tokens of answer.

**And then it is the same story this project keeps writing.** Push the knob
further and the metric it targets keeps improving -- loop rate reaches 0% at
1.2 and stays there -- while the behaviour that matters collapses. At 1.5 the
model loops literally never and stops less than half the time, with answers
two and a half times longer than the data's own. A model that cannot stop is
not a model that has been fixed.

The mechanism is not mysterious. The penalty is applied to every token in the
context, and the tokens a model needs in order to *end* -- the end-of-text
marker, the punctuation and newline patterns that precede it -- are common,
which means they are repeated, which means they are penalised. Turning up the
pressure on repetition turns up the pressure on stopping. That is the sixth
time in this project that the metric closest to the knob has ranked the
options backwards against the metric that matters.

## What ships

`repetition_penalty` defaults to **1.0 -- off** everywhere in the library, the
API and `scripts/eval_instruction_following.py`, so every number already
published in this repo stays exactly comparable and nothing measured before
today silently changes.

The browser frontend defaults its slider to **1.1**, because it is a demo
rather than a measurement, and 1.1 is better than off on both of the axes a
person actually sees. The eval preamble records the penalty a table was
produced under, so two tables run at different settings cannot be read as one
comparison.

Full generations at 1.1: [`instruction_eval_repetition_penalty.md`](instruction_eval_repetition_penalty.md).

## Reproducing this

```bash
for penalty in 1.0 1.1 1.2 1.5; do
  python scripts/eval_instruction_following.py --data data/sft/dolly.jsonl \
      --loss-batches 34 \
      --checkpoint sft=checkpoints/sft_dolly_packed3e5/step_940.pt \
      --repetition-penalty $penalty \
      --output results/instruction_eval_repetition_penalty.md
done
```
