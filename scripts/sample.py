"""Generate text from a trained AshuGPT checkpoint.

Unlike scripts/evaluate.py (which reports loss/perplexity), this just prints
completions, which is the only way to see what a checkpoint actually learned.

Uses the tiktoken GPT-2 tokenizer, matching how the `medium` run was trained.
The seed is re-applied before each prompt, so any single sample reproduces on
its own rather than only as part of a full batch.

Usage:
    python scripts/sample.py --checkpoint checkpoints/medium/step_20000.pt \
        --temperature 0.8 --top-k 50 "The process of photosynthesis"
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import torch

from ashugpt.data.chat import ASSISTANT_MARKER, SYSTEM_MARKER, USER_MARKER, Conversation, Turn
from ashugpt.data.instruction import InstructionExample
from ashugpt.inference.generate import generate
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training.checkpoint import load_model_for_inference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, default=Path("checkpoints/medium/step_20000.pt"))
    parser.add_argument("--max-new-tokens", type=int, default=120)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument(
        "--instruct",
        action="store_true",
        help="Wrap each prompt in the fine-tuning template (### Instruction / ### Response). "
        "Required for checkpoints from scripts/finetune.py -- an instruction-tuned model was "
        "trained to answer inside that template, and prompted bare it falls back to continuing "
        "text like the base model it came from.",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Wrap each prompt as the first user turn of a conversation (### User / ### Assistant). "
        "For checkpoints from scripts/finetune.py --format chat. Pass several prompts to hold a "
        "multi-turn conversation: each one is answered with every earlier turn still in context.",
    )
    parser.add_argument("prompts", nargs="+", help='Prompt strings; "" generates unconditionally')
    args = parser.parse_args()

    tokenizer = TiktokenBPETokenizer()
    model = load_model_for_inference(args.checkpoint).to(args.device).eval()

    print(f"# {args.checkpoint} | {model.num_parameters():,} parameters | device={args.device}")
    print(
        f"# temperature={args.temperature} top_k={args.top_k} top_p={args.top_p} "
        f"max_new_tokens={args.max_new_tokens} seed={args.seed}\n"
    )

    # In chat mode the prompts are turns of ONE conversation rather than
    # independent samples, so the model's own answers stay in context --
    # which is the only way to see whether multi-turn training did anything.
    conversation = Conversation([])

    for prompt in args.prompts:
        torch.manual_seed(args.seed)  # per-prompt, so each sample reproduces independently
        if args.chat:
            conversation.turns.append(Turn("user", prompt))
            text = conversation.render_for_generation()
        elif args.instruct:
            text = InstructionExample(prompt, "", "").prompt()
        else:
            text = prompt
        input_ids = torch.tensor([tokenizer.encode(text, add_bos=True)], device=args.device)

        start = time.time()
        with torch.no_grad():
            output_ids = generate(
                model,
                input_ids,
                max_new_tokens=args.max_new_tokens,
                temperature=args.temperature,
                top_k=args.top_k,
                top_p=args.top_p,
                eos_id=tokenizer.eos_id,
            )
        elapsed = time.time() - start

        # Actual tokens produced, not max_new_tokens: an instruction-tuned model
        # stops at EOS, and dividing the cap by the elapsed time would report a
        # rate several times the real one precisely when the model behaves best.
        generated = output_ids.shape[1] - input_ids.shape[1]

        print("=" * 78)
        print(f"PROMPT: {prompt!r}   [{generated} tokens, {generated / elapsed:.0f} tok/s]")
        print("-" * 78)
        decoded = tokenizer.decode(output_ids[0].tolist())
        if args.chat:
            # Only this turn's answer: everything before the last assistant
            # marker is conversation the reader has already seen.
            decoded = decoded.rsplit(ASSISTANT_MARKER.strip(), 1)[-1].strip()
            # A model that has not fully learned to stop will keep going and
            # write the user's next turn itself. Cut there: that text is not
            # part of its answer, and feeding it back as one would put a role
            # marker inside a turn, which Turn refuses outright.
            for marker in (USER_MARKER, ASSISTANT_MARKER, SYSTEM_MARKER):
                decoded = decoded.split(marker.strip(), 1)[0].strip()
            conversation.turns.append(Turn("assistant", decoded))
        elif args.instruct:
            # Show the answer, not the boilerplate template wrapped around it.
            decoded = decoded.split("### Response:", 1)[-1]
        print(decoded.strip())
        print()


if __name__ == "__main__":
    main()
