# 8. Holding a Conversation

File 5 taught the model to answer a question. This one is about the gap between
that and answering a question *in a conversation*, which is smaller than it
sounds in terms of code and much less obvious in terms of what it costs.

The code change is genuinely small. A training document becomes a conversation
instead of a question, every assistant turn is supervised instead of one
response, and `<|endoftext|>` comes to mean "my turn is over" instead of "the
document is over" because now there is more conversation after it. That is
`ashugpt/data/chat.py`, and `scripts/finetune.py --format chat` runs the same
loop with the same optimizer and the same masking rule.

Everything hard about this stage was in deciding what to measure.

## The window is a data decision, not a throughput one

Every other fine-tune here runs at `seq_len` 512 because Alpaca examples average
113 tokens and the window is mostly padding anyway. I nearly did the same, and
it would have quietly produced a single-turn dataset with a chat template on
top.

At 512, 48% of UltraChat conversations do not fit even their first answer, and
the average conversation that survives carries 1.45 assistant turns. At 1024,
90% survive and 77% carry two or more answers. Both configurations train, both
produce a falling loss, and only one of them is actually multi-turn. The number
that told me this was "mean assistant turns per surviving example", which is not
a number I would have thought to compute if the corpus had been shorter.

## The metric I nearly published backwards

I wrote `scripts/eval_chat.py` to measure the thing the format is supposed to
buy: does the model stop at the end of its own turn instead of carrying on and
writing the user's next message? I called it turn leak rate, reused the
single-turn eval's 200-token generation cap, and ran it.

The first table said the untuned model stopped on its own 100% of the time and
every chat-trained checkpoint stopped 57-65% of the time. Read literally: chat
training broke stopping.

It did not. UltraChat's held-out answers average 296 tokens and 71% of them are
longer than 200. Dolly's average 88. At a 200-token cap the "stop rate" is not
measuring whether a model finishes its turn, it is measuring whether it writes
answers shorter than most real ones — and the instruction-tuned model wins that
by answering a 296-token question in 42 tokens, which is not concision, it is
answering a different question.

The fix is a 400-token cap and a `mean reference tokens` column beside the
model's own length, so nobody can read one without the other. But the thing
worth remembering is how close that table came to being published, and that
nothing about it looked wrong. It had four checkpoints, consistent numbers, and
a clean story. It was just built on a constant I had copied from a script
written for a corpus three times shorter.

That is the fourth or fifth time on this project that a measurement has been
confidently wrong in a way the measurement itself could not show me. The
pattern is always the same shape: a number that is correct as computed, and a
sentence about the model that does not follow from it.

## What it cost the model that already worked

Scored on the held-out Dolly split, the chat-trained checkpoints are worse than
the model they started from: 0.08 to 0.17 nats of held-out loss, and single-turn
answers 50-75% longer.

That is the same relocation effect file 5 found between the Alpaca and Dolly
stages, and I think it is the most useful recurring lesson in this whole
project. A fine-tuning stage does not make a model better. It moves where the
model is good, and it moves it toward the distribution you fine-tuned on.
UltraChat's idiom is long and discursive; Dolly's is short and factual; the
model cannot be at home in both, and every stage after the first is a trade.

Whether that trade is a cost depends entirely on which distribution you meant
to serve, and that question does not have a measurement — it has an answer you
have to decide on before you start.
