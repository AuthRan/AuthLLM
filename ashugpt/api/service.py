"""The only place the API layer touches AshuGPT's actual model/tokenizer/
generation code -- ashugpt/api/app.py never imports ashugpt.model,
ashugpt.tokenizer, or ashugpt.inference.generate directly, only this
module. Keeps the HTTP layer swappable (or testable in isolation) without
touching model code, and keeps model code with zero knowledge that an API
exists at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import torch

from ashugpt.data.chat import ASSISTANT_MARKER, USER_MARKER, Conversation, Turn
from ashugpt.data.instruction import InstructionExample
from ashugpt.inference.generate import generate, generate_stream
from ashugpt.tokenizer import load_tokenizer
from ashugpt.training.checkpoint import load_model_for_inference

RESPONSE_MARKER = "### Response:\n"


@dataclass
class GenerateResult:
    generated_text: str
    tokens_generated: int
    generation_time: float
    tokens_per_second: float


@dataclass
class StreamChunk:
    """One step of a streaming generation: the new text, and the counters
    a client needs to show progress without recomputing anything."""

    text: str
    tokens_generated: int
    done: bool


class InferenceService:
    """Holds one loaded model + tokenizer in memory. Constructed once at
    server startup (see app.py's lifespan handler) and reused for every
    request -- loading (reading the checkpoint file, constructing the
    nn.Module, moving weights into it) happens exactly once, not per call."""

    def __init__(
        self,
        model: torch.nn.Module,
        tokenizer,
        checkpoint_path: str,
        device: str = "cpu",
    ) -> None:
        self.model = model.to(device)
        self.tokenizer = tokenizer
        self.checkpoint_path = checkpoint_path
        self.device = device
        self.model.eval()

    @classmethod
    def load(
        cls,
        checkpoint_path: str | Path,
        tokenizer_path: str | Path,
        device: str | None = None,
    ) -> InferenceService:
        """`load_tokenizer`, not `BPETokenizer.load`: the two tokenizers in
        this repo have different id spaces, and a checkpoint is only valid
        with the one it was trained on. The 124M runs used the tiktoken
        GPT-2 vocabulary, so hard-coding the from-scratch class here made
        the server unable to serve the only large model this project has --
        and it would not have failed loudly, it would have produced fluent
        nonsense from mismatched ids."""
        tokenizer = load_tokenizer(tokenizer_path)
        model = load_model_for_inference(checkpoint_path)
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        return cls(model=model, tokenizer=tokenizer, checkpoint_path=str(checkpoint_path), device=device)

    def _encode_prompt(
        self,
        prompt: str,
        instruct: bool,
        chat: bool = False,
        history: Sequence[tuple[str, str]] = (),
    ) -> tuple[torch.Tensor, str]:
        """Returns the model input and the exact text that was fed to it.

        Three document formats, one per training stage, and a checkpoint
        answers only in the one it was trained on:

        - bare `prompt` -- the base model's format, text continuation.
        - `instruct=True` -- the single-turn template. An instruction-tuned
          checkpoint was trained to answer *inside* it; prompted bare it
          falls back to continuing text like the base model it came from,
          which looks like a broken fine-tune rather than a misused one
          (README section 10.1).
        - `chat=True` -- the multi-turn template, with `history` replayed
          ahead of `prompt` and the assistant marker left open at the end
          so the model knows the next turn is its own (README section 10.7).

        The conversation is rebuilt from scratch on every request because
        the server holds no session state: the client owns the history,
        which is what makes two browser tabs independent.
        """
        if instruct and chat:
            raise ValueError("instruct and chat are different document formats; ask for at most one")

        if chat:
            turns = [Turn(role=role, content=content) for role, content in history]
            turns.append(Turn(role="user", content=prompt))
            text = Conversation(turns=turns).render_for_generation()
        elif instruct:
            text = InstructionExample(prompt, "", "").prompt()
        else:
            text = prompt
        input_ids = torch.tensor([self.tokenizer.encode(text, add_bos=True)], device=self.device)
        return input_ids, text

    def generate(
        self,
        prompt: str,
        max_new_tokens: int,
        temperature: float,
        top_k: int | None,
        top_p: float | None,
        repetition_penalty: float = 1.0,
        instruct: bool = False,
        chat: bool = False,
        history: Sequence[tuple[str, str]] = (),
    ) -> GenerateResult:
        """Raises ValueError for requests the model itself rejects (e.g.
        prompt + max_new_tokens exceeding context_length, or an invalid
        temperature/top_k/top_p) -- the caller (app.py) maps that to a 400,
        distinct from an actual server-side failure."""
        input_ids, _ = self._encode_prompt(prompt, instruct, chat, history)
        prompt_len = input_ids.shape[1]

        start = time.perf_counter()
        output_ids = generate(
            self.model,
            input_ids,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            eos_id=self.tokenizer.eos_id,
        )
        elapsed = time.perf_counter() - start

        tokens_generated = output_ids.shape[1] - prompt_len
        generated_text = self.tokenizer.decode(output_ids[0].tolist())
        if instruct:
            generated_text = _strip_template(generated_text)
        elif chat:
            generated_text = _strip_chat(generated_text)
        tokens_per_second = tokens_generated / elapsed if elapsed > 0 else float("inf")

        return GenerateResult(
            generated_text=generated_text,
            tokens_generated=tokens_generated,
            generation_time=elapsed,
            tokens_per_second=tokens_per_second,
        )

    def stream(
        self,
        prompt: str,
        max_new_tokens: int,
        temperature: float,
        top_k: int | None,
        top_p: float | None,
        repetition_penalty: float = 1.0,
        instruct: bool = False,
        chat: bool = False,
        history: Sequence[tuple[str, str]] = (),
    ) -> Iterator[StreamChunk]:
        """Same generation, yielded as it happens.

        Text deltas are computed by decoding the whole generated run each
        step and taking what is new, rather than decoding each token on its
        own. A BPE token is a sequence of *bytes*, not characters: a single
        token can end mid-UTF-8, and decoding it alone would emit a
        replacement character where the next token was going to complete a
        multi-byte codepoint. Decode-all-and-diff costs an O(n) decode per
        step over a few hundred tokens, which is nothing next to a forward
        pass, and is correct for every input.

        Note the difference from `generate()`, which returns the prompt and
        the continuation together: a stream yields *only* new text, because
        a client that already has the prompt on screen does not want it
        sent back a second time.
        """
        input_ids, _ = self._encode_prompt(prompt, instruct, chat, history)

        # Not a generator function, for the same reason generate_stream()
        # is not: anything that can be rejected -- an over-long prompt, an
        # invalid top_p -- must raise now, while the caller can still turn
        # it into a 400. A generator body would not run until the first
        # token was pulled, by which point an HTTP response has already
        # committed to 200 OK.
        token_stream = generate_stream(
            self.model,
            input_ids,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            eos_id=self.tokenizer.eos_id,
        )

        def chunks() -> Iterator[StreamChunk]:
            generated_ids: list[int] = []
            emitted = ""
            tokens_generated = 0

            for step in token_stream:
                generated_ids.append(int(step[0].item()))
                tokens_generated += 1

                text = self.tokenizer.decode(generated_ids)
                if not text.startswith(emitted):
                    # A newly completed codepoint can rewrite the tail of
                    # what was already decoded. Rare, but a delta computed
                    # by slicing would corrupt the output when it happens,
                    # so send nothing and let the next token settle it.
                    continue
                delta, emitted = text[len(emitted) :], text
                if delta:
                    yield StreamChunk(text=delta, tokens_generated=tokens_generated, done=False)

            yield StreamChunk(text="", tokens_generated=tokens_generated, done=True)

        return chunks()

    @property
    def parameter_count(self) -> int:
        return self.model.num_parameters()


def _strip_template(decoded: str) -> str:
    """Show the answer, not the boilerplate wrapped around it. Matches what
    scripts/sample.py --instruct prints."""
    return decoded.split(RESPONSE_MARKER.rstrip("\n"), 1)[-1].strip()


def _strip_chat(decoded: str) -> str:
    """The newest assistant turn, with the transcript before it removed.

    `generate()` returns prompt + continuation, and for a chat request the
    prompt is the whole rendered conversation -- so the answer is what
    follows the *last* assistant marker. It is then cut at the first marker
    of any kind, because a model that keeps writing past its own turn
    starts producing the user's next message, and echoing that back would
    put words in the user's mouth (README section 10.7 measures how often
    this happens: 0%, but the guard is cheap and the failure is ugly).
    """
    answer = decoded.rsplit(ASSISTANT_MARKER.rstrip("\n"), 1)[-1]
    for marker in (USER_MARKER, ASSISTANT_MARKER):
        answer = answer.split(marker.rstrip("\n"), 1)[0]
    return answer.strip()
