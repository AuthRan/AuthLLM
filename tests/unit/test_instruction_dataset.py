"""InstructionDataset: the prompt must be masked out of the loss.

A bug here is silent -- training still runs and the loss still falls, it just
falls on the wrong objective (learning to generate the instruction as well as
the answer). These tests pin the masking down against the tokenizer rather
than against hand-counted indices.
"""

from __future__ import annotations

import pytest
import torch

from ashugpt.data.instruction import (
    IGNORE_INDEX,
    InstructionDataset,
    InstructionExample,
)
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer


@pytest.fixture(scope="module")
def tokenizer():
    return TiktokenBPETokenizer()


def test_only_the_response_is_supervised(tokenizer):
    ex = InstructionExample("Name three primary colours.", "", "Red, blue, and yellow.")
    dataset = InstructionDataset([ex], tokenizer, seq_len=128)
    _, labels = dataset[0]

    supervised = labels[labels != IGNORE_INDEX]
    # decode() strips the trailing EOS, leaving exactly the response text.
    assert tokenizer.decode(supervised.tolist()) == "Red, blue, and yellow."


def test_response_ends_with_eos(tokenizer):
    """Without a terminator the model never learns to stop."""
    ex = InstructionExample("Say hi.", "", "Hi.")
    dataset = InstructionDataset([ex], tokenizer, seq_len=64)
    _, labels = dataset[0]

    supervised = labels[labels != IGNORE_INDEX]
    assert supervised[-1].item() == tokenizer.eos_id


def test_labels_are_inputs_shifted_by_one(tokenizer):
    """labels[t] must be the token following input_ids[t] -- the model does no
    shifting of its own, so an off-by-one here trains against the wrong target."""
    ex = InstructionExample("Count to three.", "", "One, two, three.")
    dataset = InstructionDataset([ex], tokenizer, seq_len=96)
    input_ids, labels = dataset[0]

    positions = (labels != IGNORE_INDEX).nonzero().flatten()
    first, last = positions[0].item(), positions[-1].item()
    assert torch.equal(input_ids[first + 1 : last + 1], labels[first:last])


def test_padding_is_masked(tokenizer):
    ex = InstructionExample("Say hi.", "", "Hi.")
    seq_len = 128
    dataset = InstructionDataset([ex], tokenizer, seq_len=seq_len)
    input_ids, labels = dataset[0]

    assert input_ids.shape == (seq_len,) and labels.shape == (seq_len,)
    padding = input_ids == tokenizer.pad_id
    assert padding.any(), "this example should be shorter than the window"
    assert (labels[padding] == IGNORE_INDEX).all()


def test_overlong_examples_are_dropped_not_truncated(tokenizer):
    """A truncated example ends mid-response, teaching the model to stop
    wherever the window happened to run out."""
    short = InstructionExample("Say hi.", "", "Hi.")
    long = InstructionExample("Recite.", "", "word " * 5000)
    dataset = InstructionDataset([short, long], tokenizer, seq_len=128)

    assert len(dataset) == 1
    assert dataset.dropped == 1


def test_input_field_selects_the_other_template(tokenizer):
    with_input = InstructionExample("Summarize.", "Some context here.", "A summary.")
    without_input = InstructionExample("Summarize.", "", "A summary.")

    assert "### Input:" in with_input.prompt()
    assert "Some context here." in with_input.prompt()
    assert "### Input:" not in without_input.prompt()
    assert both_end_in_response_header(with_input, without_input)


def both_end_in_response_header(*examples) -> bool:
    return all(ex.prompt().endswith("### Response:\n") for ex in examples)


def test_every_item_has_at_least_one_supervised_position(tokenizer):
    """An example with nothing to learn contributes a NaN-risk empty mean."""
    examples = [
        InstructionExample("A.", "", "one"),
        InstructionExample("B.", "ctx", "two"),
        InstructionExample("C.", "", "three"),
    ]
    dataset = InstructionDataset(examples, tokenizer, seq_len=64)
    for i in range(len(dataset)):
        _, labels = dataset[i]
        assert (labels != IGNORE_INDEX).any()
