"""PreferenceDataset: two answers to one prompt, encoded exactly like SFT.

The failure that matters here is not a crash. DPO measures a *difference*
between what the policy and the frozen reference assign to the same tokens,
and the reference is the SFT checkpoint -- so if these windows differ from
the ones SFT trained on by so much as the prompt template, every
log-probability in the objective is measured on text the reference has
never seen in that form. These tests hold the two encodings together.
"""

from __future__ import annotations

import pytest
import torch

from ashugpt.data.instruction import IGNORE_INDEX, InstructionDataset, InstructionExample
from ashugpt.data.preference import PreferenceDataset, PreferenceExample
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer


@pytest.fixture(scope="module")
def tokenizer():
    return TiktokenBPETokenizer()


def test_an_item_is_a_pair(tokenizer):
    """Chosen on row 0, rejected on row 1 -- the shape DPOModel unbinds."""
    ex = PreferenceExample("Name a colour.", "", "Blue.", "asdf")
    dataset = PreferenceDataset([ex], tokenizer, seq_len=128)
    input_ids, labels = dataset[0]

    assert input_ids.shape == (2, 128)
    assert labels.shape == (2, 128)
    assert tokenizer.decode(labels[0][labels[0] != IGNORE_INDEX].tolist()) == "Blue."
    assert tokenizer.decode(labels[1][labels[1] != IGNORE_INDEX].tolist()) == "asdf"


def test_both_sides_match_what_supervised_fine_tuning_would_produce(tokenizer):
    """Byte-for-byte the same windows, because the reference model is the SFT
    model and the objective compares log-probabilities of these exact tokens."""
    instruction, input_text = "Explain gravity.", "For a child."
    chosen, rejected = "Things fall down.", "Gravity is a force."

    pairs = PreferenceDataset(
        [PreferenceExample(instruction, input_text, chosen, rejected)], tokenizer, seq_len=128
    )
    sft = InstructionDataset(
        [
            InstructionExample(instruction, input_text, chosen),
            InstructionExample(instruction, input_text, rejected),
        ],
        tokenizer,
        seq_len=128,
    )

    pair_inputs, pair_labels = pairs[0]
    for row, index in ((0, 0), (1, 1)):
        sft_input, sft_labels = sft[index]
        assert torch.equal(pair_inputs[row], sft_input)
        assert torch.equal(pair_labels[row], sft_labels)


def test_the_prompt_is_shared_and_masked_on_both_sides(tokenizer):
    ex = PreferenceExample("Say hi.", "", "Hello!", "no")
    input_ids, labels = PreferenceDataset([ex], tokenizer, seq_len=64)[0]

    n_prompt = (labels[0] != IGNORE_INDEX).nonzero()[0].item()
    assert n_prompt > 0
    assert (labels[:, :n_prompt] == IGNORE_INDEX).all()
    # Same prompt tokens fed to the model on both rows.
    assert torch.equal(input_ids[0, : n_prompt + 1], input_ids[1, : n_prompt + 1])


def test_both_responses_end_with_eos(tokenizer):
    """Same terminator rule as SFT: without it neither answer teaches stopping,
    and the pair would differ from the reference model's training in a way the
    implicit reward would read as a preference."""
    ex = PreferenceExample("Say hi.", "", "Hello!", "no")
    _, labels = PreferenceDataset([ex], tokenizer, seq_len=64)[0]

    for row in (0, 1):
        supervised = labels[row][labels[row] != IGNORE_INDEX]
        assert supervised[-1].item() == tokenizer.eos_id


def test_a_pair_is_dropped_when_either_side_overruns_the_window(tokenizer):
    """Not just the offending side: comparing a whole answer against a
    truncated one would teach that fragments are bad, which is true and is
    not the preference anyone labelled."""
    long_answer = "word " * 500
    dataset = PreferenceDataset(
        [PreferenceExample("Talk.", "", "Short.", long_answer)], tokenizer, seq_len=128
    )
    assert len(dataset) == 0
    assert dataset.dropped == 1


def test_identical_answers_are_dropped(tokenizer):
    """The margin is then zero whatever the policy does: a constant log 2 of
    loss, no gradient, and a full forward pass to produce it."""
    dataset = PreferenceDataset(
        [PreferenceExample("Say hi.", "", "Hello!", "Hello!  ")], tokenizer, seq_len=64
    )
    assert len(dataset) == 0
    assert dataset.dropped == 1


def test_empty_answers_are_dropped(tokenizer):
    dataset = PreferenceDataset([PreferenceExample("Say hi.", "", "Hello!", "")], tokenizer, seq_len=64)
    assert len(dataset) == 0
    assert dataset.dropped == 1


def test_mean_response_lengths_expose_the_length_shortcut(tokenizer):
    """If chosen answers are systematically longer, "longer" predicts the
    label and a model can score perfectly by learning only that."""
    dataset = PreferenceDataset(
        [
            PreferenceExample("Q1?", "", "A considerably longer answer here.", "No."),
            PreferenceExample("Q2?", "", "Another rather long answer as well.", "Nope."),
        ],
        tokenizer,
        seq_len=128,
    )
    chosen, rejected = dataset.mean_response_lengths
    assert chosen > rejected
    assert PreferenceDataset([], tokenizer).mean_response_lengths == (0.0, 0.0)
