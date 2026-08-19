"""Instruction-tuning dataset: prompt/response pairs with the prompt masked out.

Pretraining and instruction tuning use the same objective -- predict the next
token -- but not the same *targets*. Pretraining wants the model to predict
every token in the stream. Instruction tuning only wants it to predict the
**response**: the instruction is context the model is given, not text it should
learn to produce. Training on the instruction too teaches the model to invent
plausible questions, which is exactly the behaviour a chat model must not have.

Masking is how that distinction is expressed. Label positions belonging to the
prompt are set to IGNORE_INDEX (-100), which `F.cross_entropy` skips by
default, so those positions contribute no gradient. No change to the model is
needed -- it already computes its loss with the default ignore_index.

The one-token shift convention matches ashugpt/data/dataset.py: this class
emits `input_ids` and `labels` already offset by one, so `labels[t]` is the
token that should follow `input_ids[t]`, and the model does no shifting.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch.utils.data import Dataset

IGNORE_INDEX = -100

PROMPT_WITH_INPUT = (
    "Below is an instruction that describes a task, paired with an input that "
    "provides further context. Write a response that appropriately completes "
    "the request.\n\n"
    "### Instruction:\n{instruction}\n\n"
    "### Input:\n{input}\n\n"
    "### Response:\n"
)

PROMPT_NO_INPUT = (
    "Below is an instruction that describes a task. Write a response that "
    "appropriately completes the request.\n\n"
    "### Instruction:\n{instruction}\n\n"
    "### Response:\n"
)


@dataclass
class InstructionExample:
    instruction: str
    input: str
    output: str

    def prompt(self) -> str:
        if self.input.strip():
            return PROMPT_WITH_INPUT.format(instruction=self.instruction.strip(), input=self.input.strip())
        return PROMPT_NO_INPUT.format(instruction=self.instruction.strip())


class InstructionDataset(Dataset):
    """Tokenized prompt/response pairs, padded to a fixed length.

    Every item is (input_ids, labels), both length `seq_len`. Positions that
    should not be learned -- the prompt, and any right-padding -- carry
    IGNORE_INDEX in `labels`.

    Examples longer than the window are dropped rather than truncated. A
    truncated example ends mid-response, which teaches the model to stop
    abruptly at exactly the point the window runs out; at 512 tokens this
    discards a small fraction of Alpaca and Dolly.
    """

    def __init__(self, examples: list[InstructionExample], tokenizer, seq_len: int = 512) -> None:
        self.seq_len = seq_len
        self.tokenizer = tokenizer
        self.pad_id = tokenizer.pad_id

        self.encoded: list[tuple[list[int], int]] = []
        self.dropped = 0
        for ex in examples:
            prompt_ids = tokenizer.encode(ex.prompt(), add_bos=True)
            response_ids = tokenizer.encode(ex.output.strip(), add_eos=True)
            ids = prompt_ids + response_ids
            # +1 because the window is sliced into seq_len inputs and seq_len
            # labels, which needs seq_len + 1 tokens.
            if len(ids) > seq_len + 1 or not response_ids:
                self.dropped += 1
                continue
            self.encoded.append((ids, len(prompt_ids)))

    def __len__(self) -> int:
        return len(self.encoded)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        ids, n_prompt = self.encoded[idx]

        input_ids = torch.full((self.seq_len,), self.pad_id, dtype=torch.long)
        labels = torch.full((self.seq_len,), IGNORE_INDEX, dtype=torch.long)

        body = torch.tensor(ids, dtype=torch.long)
        n_in = min(len(ids) - 1, self.seq_len)
        input_ids[:n_in] = body[:n_in]
        labels[:n_in] = body[1 : n_in + 1]

        # labels[t] predicts ids[t + 1], so the first position whose target is
        # a response token is t = n_prompt - 1. Everything before that is the
        # prompt predicting itself, and is masked.
        labels[: n_prompt - 1] = IGNORE_INDEX

        return input_ids, labels
