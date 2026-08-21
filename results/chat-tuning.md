# What Multi-Turn Training Actually Changed

The instruction-tuned model answers one question and stops. That is what
[instruction-tuning.md](instruction-tuning.md) bought, and it is exactly as far
as a single-turn template can go: one instruction slot, one response slot, and
nowhere to put what was said before.

This page is about giving it conversations instead — 20,000 UltraChat
dialogues, every assistant turn supervised, `<|endoftext|>` ending a *turn*
rather than a document ([§10.7](../README.md#107-multi-turn-chat--a-conversation-not-a-question)).
Two things came out of it that I did not expect, and one of them is that the
first metric I reached for was measuring the wrong thing entirely.

## The window decides how multi-turn the corpus is

Every other fine-tuning stage in this repo runs at `seq_len` 512. This one runs
at 1024, and that is not a throughput decision. Measured over the 19,600
conversation training split, by how much of a conversation fits before
`ChatDataset` has to cut at a turn boundary:

| seq_len | usable | dropped | supervised | mean assistant turns | ≥2 answers |
|---:|---:|---:|---:|---:|---:|
| 512 | 10,203 | 9,397 | 50.7% | 1.45 | 36% |
| 768 | 15,149 | 4,451 | 54.1% | 1.84 | 60% |
| 1024 | 17,672 | 1,928 | 54.4% | 2.20 | 77% |

At 512 the corpus is barely multi-turn — 48% of conversations do not fit even
their first answer, and the average survivor carries 1.45 assistant turns,
which is a single-turn dataset with a chat template on top. Training a
multi-turn model on that would have produced a completely believable loss curve
and no new behaviour.

The supervised fraction is the other number worth reading: instruction tuning
supervised about 11% of every step, because a short answer sat in a mostly
padded window. Here the assistant does most of the talking and 54% of every
window produces gradient — which is also why sequence packing is not wired up
for conversations. There is almost no empty window left to reclaim.

## The metric that was measuring the wrong thing

`scripts/eval_chat.py` started life with the same 200-token generation cap the
single-turn eval uses, and the first table it produced said the chat models had
*broken* stopping: the untuned model stopped on its own 100% of the time, and
every chat-trained checkpoint stopped only 57-65% of the time.

That reading is an artifact. UltraChat's held-out final assistant turns average
**296 tokens** and 71% of them are longer than 200. Dolly's average 88. So at a
200-token cap the stop rate is not measuring "does the model finish its turn",
it is measuring "does the model write answers shorter than most real ones" —
and the instruction-tuned model aces it by answering a 296-token question in 42
tokens, which is not brevity, it is a different task.

The cap is 400 now and the table reports the reference length beside the
model's own, because an answer length means nothing on its own.

I am writing this down because the wrong version of that table was one edit
away from being published, and it would have said something confident and
backwards about the stage.

## The table, once the cap was right

Four learning rates, 300 steps each, complete cosine cycles so the endpoints
compare. Scored on 40 held-out conversations, each prompted with its full
history up to its last assistant turn:

| checkpoint | held-out loss | stop rate | turn leak | mean tokens | loop rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sft` — before chat training | 2.8262 | 100% | 0% | 57 | 5% |
| chat, 1.5e-5 | 2.1187 | 88% | 0% | 211 | 48% |
| chat, 4.0e-5 | 2.0630 | 88% | 0% | 183 | **30%** |
| **chat, 1.0e-4** | **2.0333** | 98% | 0% | 182 | 35% |
| chat, 2.5e-4 | 2.0545 | **100%** | 0% | 153 | 38% |
| *the human-written answers* | — | — | — | *222* | *15%* |

Held-out loss drops 0.79 nats, 2.8262 → 2.0333, which is the largest
single-stage improvement anywhere in this project — unsurprising, since the SFT
model had never seen this document format at all. 1.0e-4 wins it, 2.5x the
value the config originally argued for by analogy, and 2.5e-4 is past the
optimum on both loss and answer length, so the minimum is bracketed rather than
sitting at the edge of the range I happened to try.

The two right-hand columns are the ones that need their bottom row. The chat
models answer at 153-211 tokens against references averaging 222; the untuned
model answers at 57. And 15% of the *human* answers trip the loop detector at
that length, so the chat models' 30-48% is genuinely elevated but nothing like
the tenfold jump you would read off the untuned model's 5%.

**Turn leak rate is 0% in every row**, which is the result that surprised me.
The whole design story of this format is that `<|endoftext|>` ending a *turn*
rather than a *document* is what stops a model writing the user's next message
— and the model that never learned that never does it either. It emits EOS
promptly, because one-answer-per-document taught it exactly that. Its failure
on a conversation is quieter: it treats the dialogue as a one-shot prompt and
answers a 222-token question in 57 tokens.

So the honest version of what this stage buys is "answers at the length and in
the register a conversation calls for", not "stops in the right place". That is
a smaller claim than the format's design implies, and it is the one the numbers
support.

## What multi-turn training costs the single-turn model

The same held-out Dolly split, the same script and settings as every row in
[instruction-tuning.md](instruction-tuning.md):

| checkpoint | Dolly held-out loss | stop rate | mean tokens | loop rate |
| --- | ---: | ---: | ---: | ---: |
| `sft` — before any chat training | **2.7444** | 92% | **62** | **20%** |
| chat, 1.5e-5 | 2.8244 | 80% | 102 | 28% |
| chat, 4.0e-5 | 2.8530 | 75% | 109 | 38% |
| chat, 1.0e-4 | 2.9178 | 88% | 97 | 22% |

Chat training costs 0.08 to 0.17 nats on the single-turn distribution and makes
single-turn answers 50-75% longer. That is the same relocation effect
[§10.4](../README.md#104-what-it-changed-measured) found between the Alpaca and
Dolly stages: a stage does not make the model better, it moves where the model
is good, and UltraChat's idiom is long and discursive where Dolly's is short.

Whether that is a cost or the entire point depends on which distribution you
intend to serve. It is a cost if you wanted the Dolly model. It is the goal if
you wanted a model that can hold a conversation.
