"""Measure whether a model can hold a conversation, rather than answer once.

`scripts/eval_instruction_following.py` scores a single-turn model: did it
answer, did it stop, did it loop. A chat model has to do all of that *and* one
more thing, which is the entire claim behind
[README section 10.7](../README.md#107-multi-turn-chat--a-conversation-not-a-question):
stop at the end of **its own turn** instead of carrying on and writing the
user's next message too.

That claim is testable, and until this script existed it was only asserted.
The metric is **turn leak rate**: prompt the model with a conversation that
ends where the assistant should start, generate, and check whether a role
marker (`### User:` / `### Assistant:`) appears in what comes back. A model
trained on single-turn data has never seen `<|endoftext|>` mean anything but
"the document is over", so nothing stops it from inventing the next question;
a model trained on conversations has seen the token end a turn thousands of
times with more conversation after it.

Alongside it, the same three behavioural numbers §10.4 uses, so the columns
are readable next to that table, plus held-out loss over every assistant turn.

**The generation cap is 400 tokens, not the 200 the single-turn eval uses**,
and that difference is not cosmetic. UltraChat's held-out final assistant turns
average 296 tokens and 71% of them are longer than 200, against Dolly answers
averaging 88. At a 200-token cap the stop rate stops measuring "does the model
finish its turn" and starts measuring "does the model write answers shorter
than most real ones", which a model tuned on Dolly passes by being far too
terse. A `mean reference tokens` column is reported beside the model's own
length for the same reason: an answer length only means something next to the
length the data actually has.

The table's last row is the **human-written reference answers**, scored by the
identical loop detector. Loop rate counts a repeated ten-token window anywhere
in a generation, so a model whose answers are four times longer has four times
the opportunity to trip it -- and on this corpus 15% of the real answers do.
Without that row, "2% before tuning, 48% after" reads as catastrophic
degeneration when most of the change is length.

The prompt is always the conversation's **last** assistant turn with all of
its history in front of it -- the hardest and most representative case, and
the one a first-turn-only evaluation would never exercise.

Usage:
    python scripts/eval_chat.py --data data/sft/ultrachat.jsonl \
        --checkpoint sft=checkpoints/sft_dolly_packed3e5/step_940.pt \
        --checkpoint chat=checkpoints/sft_chat/step_1105.pt \
        --output results/chat_eval.md
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ashugpt.data.chat import ASSISTANT_MARKER, SYSTEM_MARKER, USER_MARKER, ChatDataset, Conversation
from ashugpt.eval.generation import has_repeated_window
from ashugpt.eval.perplexity import evaluate
from ashugpt.inference.generate import generate
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training.checkpoint import load_model_for_inference

MARKERS = tuple(marker.strip() for marker in (SYSTEM_MARKER, USER_MARKER, ASSISTANT_MARKER))


def read_conversations(path: Path) -> list[Conversation]:
    with path.open(encoding="utf-8") as fh:
        return [Conversation.from_messages(json.loads(line)["messages"]) for line in fh]


def held_out_split(conversations: list[Conversation], seed: int, val_fraction: float) -> list[Conversation]:
    """The same slice scripts/finetune.py held out -- same shuffle, same cut."""
    conversations = list(conversations)
    random.Random(seed).shuffle(conversations)
    n_val = max(1, int(len(conversations) * val_fraction))
    return conversations[:n_val]


def measure_generation(model, tokenizer, conversations, args) -> tuple[dict[str, float], list[dict]]:
    device = next(model.parameters()).device
    records = []
    for conversation in conversations:
        prepared = conversation.split_last_answer()
        if prepared is None:
            continue
        history, answer = prepared
        prompt, reference = history.render_for_generation(), answer.content
        prompt_ids = tokenizer.encode(prompt, add_bos=True)
        # A prompt that leaves no room to answer measures the window, not the
        # model. Skipped rather than truncated: cutting the history would
        # change the question being asked.
        if len(prompt_ids) + args.max_new_tokens > args.seq_len:
            continue

        torch.manual_seed(args.seed)  # per prompt, so any row reproduces alone
        output_ids = generate(
            model,
            torch.tensor([prompt_ids], device=device),
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_k=args.top_k,
            eos_id=tokenizer.eos_id,
        )
        new_ids = output_ids[0, len(prompt_ids) :].tolist()
        stopped = tokenizer.eos_id in new_ids
        answer_ids = new_ids[: new_ids.index(tokenizer.eos_id)] if stopped else new_ids
        raw_answer = tokenizer.decode(answer_ids)

        # The leak: the model kept going past its own turn and started
        # another one. Measured on the text *before* any cleanup, because
        # scripts/sample.py cuts at the marker for display and that cut is
        # exactly what would hide this.
        reference_ids = tokenizer.encode(reference)
        leaked = next((m for m in MARKERS if m in raw_answer), None)
        answer = raw_answer.split(leaked, 1)[0].strip() if leaked else raw_answer.strip()

        records.append(
            {
                "prompt": prompt,
                "reference": reference,
                "answer": answer,
                "turns_of_history": len(conversation.turns) - 1,
                "stopped": stopped,
                "leaked": leaked,
                "tokens": len(answer_ids),
                "reference_tokens": len(reference_ids),
                "reference_looped": has_repeated_window(reference_ids),
                "looped": has_repeated_window(answer_ids),
            }
        )
        if len(records) >= args.n_prompts:
            break

    if not records:
        raise ValueError("no held-out conversation produced a prompt that fits the window")

    n = len(records)
    return (
        {
            "stop_rate": sum(r["stopped"] for r in records) / n,
            "leak_rate": sum(r["leaked"] is not None for r in records) / n,
            "mean_tokens": sum(r["tokens"] for r in records) / n,
            "mean_reference_tokens": sum(r["reference_tokens"] for r in records) / n,
            "reference_loop_rate": sum(r["reference_looped"] for r in records) / n,
            "loop_rate": sum(r["looped"] for r in records) / n,
            "prompts": n,
        },
        records,
    )


def parse_checkpoint(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"--checkpoint wants name=path, got {value!r}")
    name, path = value.split("=", 1)
    return name, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="Conversation JSONL to hold examples out of")
    parser.add_argument("--checkpoint", type=parse_checkpoint, action="append", required=True)
    parser.add_argument("--val-fraction", type=float, default=0.02, help="Must match the fine-tune's value")
    parser.add_argument("--split-seed", type=int, default=1337, help="Must match the fine-tune's train config seed")
    parser.add_argument("--loss-batches", type=int, default=40, help="Batches of held-out loss to average")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--seq-len", type=int, default=1024)
    parser.add_argument("--n-prompts", type=int, default=40, help="Held-out conversations to generate from")
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=400,
        help="Higher than the single-turn eval's 200 on purpose -- see the module docstring",
    )
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--output", type=Path, default=None, help="Write every generation to this markdown file")
    args = parser.parse_args()

    tokenizer = TiktokenBPETokenizer()
    held_out = held_out_split(read_conversations(args.data), args.split_seed, args.val_fraction)
    dataset = ChatDataset(held_out, tokenizer, seq_len=args.seq_len)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=False)
    print(
        f"{len(held_out):,} held-out conversations, {len(dataset):,} usable "
        f"({dataset.supervised_fraction:.1%} of positions supervised)"
    )

    rows, all_records = {}, {}
    for name, path in args.checkpoint:
        model = load_model_for_inference(path).to(args.device).eval()
        loss = evaluate(model, loader, amp_dtype=None, max_batches=args.loss_batches)
        summary, records = measure_generation(model, tokenizer, held_out, args)
        rows[name] = {**loss, **summary}
        all_records[name] = records
        print(
            f"{name:>10}: loss {loss['loss']:.4f} | stop {summary['stop_rate']:.0%} | "
            f"turn leak {summary['leak_rate']:.0%} | {summary['mean_tokens']:.0f} tokens "
            f"(reference {summary['mean_reference_tokens']:.0f}) | "
            f"loop {summary['loop_rate']:.0%} (reference {summary['reference_loop_rate']:.0%})"
            f"  ({summary['prompts']} prompts)"
        )
        del model
        if args.device.startswith("cuda"):
            torch.cuda.empty_cache()

    if args.output is None:
        return

    lines = [
        f"# Multi-turn chat eval — `{args.data.name}`",
        "",
        f"{len(held_out):,} held-out conversations (seed {args.split_seed}, val fraction "
        f"{args.val_fraction}); loss over {args.loss_batches} batches of {args.batch_size}; generations "
        f"from up to {args.n_prompts} held-out conversations, each prompted with its full history up to "
        f"its last assistant turn, at temperature {args.temperature}, top_k {args.top_k}, cap "
        f"{args.max_new_tokens} tokens.",
        "",
        "**Turn leak rate** is the fraction of answers containing a role marker — the model finished its "
        "turn and carried on writing the other side of the conversation. It is measured before the "
        "marker-cut that `scripts/sample.py` applies for display, because that cut is exactly what would "
        "hide it.",
        "",
        f"The last row is the human-written reference answers, scored the same way. It is there because "
        f"neither of the two right-hand columns means anything on its own: an answer length has to be read "
        f"against the length the data actually has, and loop rate counts a repeated ten-token window "
        f"anywhere in a generation, so a model answering four times longer gets four times the chance to "
        f"trip it.",
        "",
        "| checkpoint | held-out loss | stop rate | turn leak rate | mean tokens | loop rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in rows.items():
        lines.append(
            f"| `{name}` | {metrics['loss']:.4f} | {metrics['stop_rate']:.0%} | "
            f"{metrics['leak_rate']:.0%} | {metrics['mean_tokens']:.0f} | {metrics['loop_rate']:.0%} |"
        )
    reference = next(iter(rows.values()))
    lines.append(
        f"| *the human answers themselves* | — | — | — | "
        f"{reference['mean_reference_tokens']:.0f} | {reference['reference_loop_rate']:.0%} |"
    )

    for name, records in all_records.items():
        lines += ["", f"## {name}", ""]
        for record in records:
            leak = f" — **leaked `{record['leaked']}`**" if record["leaked"] else ""
            lines += [
                f"### {record['turns_of_history']} turns of history",
                "",
                f"`{record['tokens']} tokens, {'stopped' if record['stopped'] else 'hit the cap'}{leak}`",
                "",
                "> " + (record["answer"] or "*(empty)*").replace("\n", "\n> "),
                "",
                f"*Reference ({record['reference_tokens']} tokens):* {record['reference'][:400]}",
                "",
            ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
