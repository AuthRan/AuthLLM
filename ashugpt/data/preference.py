"""Preference pairs: one prompt, two answers, and which one is better.

Every dataset above this one -- instruction.py, chat.py -- is *imitation*
data. It holds text a human wrote and asks the model to reproduce it, so
the only thing it can express is "this is what a good answer looks like".
It has no way to say that one answer is better than another, because it
never contains two.

A preference example does. The prompt is shared, the two responses differ,
and a human (or a stronger model) has ranked them. That is what
`ashugpt/training/dpo.py` needs: its loss is a comparison, so its data has
to come in pairs.

**An item is (2, seq_len), not (seq_len,).** Chosen on row 0, rejected on
row 1. Batching then produces (batch, 2, seq_len), which `DPOModel`
flattens into a single (2 * batch, seq_len) forward pass. Keeping the pair
together inside one item is what makes that work: a DataLoader shuffles
items, and two answers to the same prompt that landed in different batches
could not be compared at all.

**Both sides are encoded exactly the way supervised fine-tuning encodes an
answer** -- same prompt template, same one-token shift, same prompt mask --
by calling the same `encode_supervised`/`render_window` that
`InstructionDataset` calls. This is not tidiness. DPO's loss is a
*difference* between what the policy and the reference assign to the same
tokens, and the reference model is the SFT checkpoint. If preference
encoding drifted from SFT encoding by so much as a space in the template,
every log-probability in the objective would be measured on text the
reference model had never seen in that form, and the run would spend its
first steps learning the new formatting rather than the preference.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch.utils.data import Dataset

from ashugpt.data.instruction import InstructionExample, encode_supervised, render_window


@dataclass
class PreferenceExample:
    instruction: str
    input: str
    chosen: str
    rejected: str

    def prompt(self) -> str:
        """The same prompt an `InstructionExample` would render.

        Delegated rather than reimplemented: the template that the SFT
        stages trained on is the template the DPO stage has to keep using.
        """
        return InstructionExample(self.instruction, self.input, "").prompt()


class PreferenceDataset(Dataset):
    """Tokenized preference pairs, each item a (2, seq_len) chosen/rejected pair.

    `input_ids[0]` and `labels[0]` are the chosen answer's window;
    `input_ids[1]` and `labels[1]` are the rejected answer's. Both carry
    IGNORE_INDEX over the shared prompt and over padding, so only response
    tokens reach the objective.

    A pair is dropped when either side fails to encode:

    - **Either response overruns the window.** Both sides must be whole for
      the comparison to mean anything. Keeping the chosen answer and
      truncating the rejected one would compare a complete answer against
      a fragment, and the model would learn that fragments are bad -- true,
      but not the preference anyone labelled.
    - **The two responses are identical.** The margin is then exactly zero
      whatever the policy does, so the pair contributes a constant log 2 to
      the loss and no gradient at all: pure padding in the dataset with a
      full forward pass as its price.
    """

    def __init__(self, examples: list[PreferenceExample], tokenizer, seq_len: int = 512) -> None:
        self.seq_len = seq_len
        self.tokenizer = tokenizer
        self.pad_id = tokenizer.pad_id

        # (chosen_ids, chosen_n_prompt, rejected_ids, rejected_n_prompt)
        self.encoded: list[tuple[list[int], int, list[int], int]] = []
        self.dropped = 0
        for ex in examples:
            if ex.chosen.strip() == ex.rejected.strip():
                self.dropped += 1
                continue
            prompt = ex.prompt()
            chosen = encode_supervised(tokenizer, prompt, ex.chosen, seq_len)
            rejected = encode_supervised(tokenizer, prompt, ex.rejected, seq_len)
            if chosen is None or rejected is None:
                self.dropped += 1
                continue
            self.encoded.append((chosen[0], chosen[1], rejected[0], rejected[1]))

    def __len__(self) -> int:
        return len(self.encoded)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        chosen_ids, chosen_n_prompt, rejected_ids, rejected_n_prompt = self.encoded[idx]

        chosen_input, chosen_labels = render_window(chosen_ids, chosen_n_prompt, self.seq_len, self.pad_id)
        rejected_input, rejected_labels = render_window(
            rejected_ids, rejected_n_prompt, self.seq_len, self.pad_id
        )

        return (
            torch.stack([chosen_input, rejected_input]),
            torch.stack([chosen_labels, rejected_labels]),
        )

    @property
    def mean_response_lengths(self) -> tuple[float, float]:
        """Mean response tokens per side, as (chosen, rejected).

        Worth printing before every run rather than after. DPO has a
        well-documented tendency to make answers longer, and the usual
        reason is that the data asked it to: if the chosen answers in a
        preference set are systematically longer than the rejected ones,
        then "longer" is a feature that predicts the label, and a model
        that learns only that feature scores perfectly on the training
        objective while getting no better at answering. The gap between
        these two numbers is how much of that shortcut the data contains.
        """
        if not self.encoded:
            return (0.0, 0.0)
        chosen = sum(len(ids) - n for ids, n, _, _ in self.encoded)
        rejected = sum(len(ids) - n for _, _, ids, n in self.encoded)
        return (chosen / len(self.encoded), rejected / len(self.encoded))
