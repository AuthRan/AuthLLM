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

import bisect
from bisect import insort
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


class PackedInstructionDataset(Dataset):
    """Several instruction examples per window, with attention masked between them.

    `InstructionDataset` pads every example out to `seq_len` on its own. Alpaca
    examples average 113 tokens against a 512-token window, so about 78% of
    every optimizer step is padding, and another 11% is prompt that the label
    mask throws away: roughly 89% of the compute produces no gradient. The
    forward/backward cost is set by the tensor shape, so that waste is real
    GPU time, not just idle memory.

    Packing fills the window instead. Each item holds as many whole examples as
    fit, laid out end to end, and two extra tensors describe the seam:

    - `segment_ids` marks which example each position belongs to, so attention
      can be blocked across the boundary (see `segment_causal_mask`). Without
      it, example 2 would attend to example 1 -- conditioning on context that
      will never exist at inference time.
    - `position_ids` restarts at 0 for each example, so RoPE sees a normal
      short document rather than one example sitting at position 300.

    With both, a packed example is mathematically identical to the same example
    run alone: same attention pattern, same positions, same masked labels. The
    only thing that changes is how many of them ride along in one forward pass.
    `tests/unit/test_packed_instruction_dataset.py` asserts that equality
    against real model logits rather than trusting the argument.

    Examples are laid out with the same one-token shift as the unpacked class,
    applied *per example*: an example of L tokens occupies L - 1 positions,
    where input is ids[:-1] and labels are ids[1:]. Shifting the packed window
    as a whole would make the last position of one example predict the first
    token of the next, which is exactly the cross-example leakage the mask
    exists to prevent.

    Padding, when a window cannot be filled exactly, carries segment id
    PAD_SEGMENT. That is deliberate rather than incidental: padded rows then
    still attend to themselves, and a row that could attend to nothing at all
    would softmax over an all -inf score row and produce NaN -- which would
    poison the whole batch's gradient even though the padded positions'
    labels are masked out of the loss.
    """

    PAD_SEGMENT = -1

    def __init__(
        self,
        examples: list[InstructionExample],
        tokenizer,
        seq_len: int = 512,
    ) -> None:
        self.seq_len = seq_len
        self.tokenizer = tokenizer
        self.pad_id = tokenizer.pad_id

        encoded: list[tuple[list[int], int]] = []
        self.dropped = 0
        for ex in examples:
            prompt_ids = tokenizer.encode(ex.prompt(), add_bos=True)
            response_ids = tokenizer.encode(ex.output.strip(), add_eos=True)
            ids = prompt_ids + response_ids
            # An example of L tokens costs L - 1 positions once shifted, so the
            # limit is seq_len + 1 raw tokens -- same rule as the unpacked
            # class, which needs seq_len inputs and seq_len labels from them.
            if len(ids) > seq_len + 1 or not response_ids:
                self.dropped += 1
                continue
            encoded.append((ids, len(prompt_ids)))

        self.encoded = encoded
        self.bins = _pack_bins([len(ids) - 1 for ids, _ in encoded], seq_len)

    def __len__(self) -> int:
        return len(self.bins)

    @property
    def packing_efficiency(self) -> float:
        """Fraction of positions across all windows that hold a real token."""
        if not self.bins:
            return 0.0
        used = sum(len(self.encoded[i][0]) - 1 for b in self.bins for i in b)
        return used / (len(self.bins) * self.seq_len)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        input_ids = torch.full((self.seq_len,), self.pad_id, dtype=torch.long)
        labels = torch.full((self.seq_len,), IGNORE_INDEX, dtype=torch.long)
        segment_ids = torch.full((self.seq_len,), self.PAD_SEGMENT, dtype=torch.long)
        position_ids = torch.zeros((self.seq_len,), dtype=torch.long)

        cursor = 0
        for segment, example_idx in enumerate(self.bins[idx]):
            ids, n_prompt = self.encoded[example_idx]
            body = torch.tensor(ids, dtype=torch.long)
            width = len(ids) - 1  # positions this example occupies once shifted
            end = cursor + width

            input_ids[cursor:end] = body[:width]
            labels[cursor:end] = body[1 : width + 1]
            # Same rule as the unpacked class, in this example's own frame:
            # labels[t] predicts ids[t + 1], so the first position whose target
            # is a response token is n_prompt - 1 from this example's start.
            labels[cursor : cursor + n_prompt - 1] = IGNORE_INDEX
            segment_ids[cursor:end] = segment
            position_ids[cursor:end] = torch.arange(width, dtype=torch.long)

            cursor = end

        return input_ids, labels, segment_ids, position_ids


def _pack_bins(sizes: list[int], capacity: int) -> list[list[int]]:
    """Best-fit-decreasing bin packing: which example indices share a window.

    Bin packing is NP-hard; best-fit-decreasing is the standard cheap
    approximation and lands within a few percent of optimal in practice, which
    is far more than enough when the alternative is a 22%-full window. Items
    are placed largest first into the tightest bin that still fits them, so the
    awkward long examples get placed while there is still room to choose, and
    the short ones fill the gaps they leave.

    The bisect-over-remaining-space bookkeeping keeps this O(n log n): a naive
    scan of every open bin per item is O(n * bins), which at Alpaca's 50k
    examples is tens of millions of Python-level comparisons.
    """
    order = sorted(range(len(sizes)), key=lambda i: sizes[i], reverse=True)
    bins: list[list[int]] = []
    # Sorted (remaining_space, bin_index), searched with bisect for the
    # smallest remaining space that still fits the item.
    open_bins: list[tuple[int, int]] = []

    for i in order:
        size = sizes[i]
        slot = bisect.bisect_left(open_bins, (size, -1))
        if slot < len(open_bins):
            remaining, bin_idx = open_bins.pop(slot)
            bins[bin_idx].append(i)
            insort(open_bins, (remaining - size, bin_idx))
        else:
            bins.append([i])
            insort(open_bins, (capacity - size, len(bins) - 1))

    return bins
