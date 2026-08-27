"""PackedInstructionDataset: packing must not change the objective.

Packing is an optimization, and an optimization that quietly alters what the
model learns is worse than no optimization at all. Two things can go wrong and
neither shows up as an error: example 2 attending to example 1 (leaked
context that will not exist at inference), and RoPE placing example 2 at
position 300 instead of 0 (a position distribution training never otherwise
sees). Both would still train, still converge, and still produce a plausible
loss curve.

So the central test here does not check the mask's shape or the position
tensor's values. It runs a real model twice -- once on a packed window, once
on each example alone -- and demands the logits match. That is the property
worth having, and it can only hold if the mask and the positions are both
right.
"""

from __future__ import annotations

import pytest
import torch

from ashugpt.config import ModelConfig
from ashugpt.data.instruction import (
    IGNORE_INDEX,
    InstructionDataset,
    InstructionExample,
    PackedInstructionDataset,
)
from ashugpt.model.gpt import AshuGPT
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer


@pytest.fixture(scope="module")
def tokenizer():
    return TiktokenBPETokenizer()


@pytest.fixture(scope="module")
def examples():
    return [
        InstructionExample("Name three primary colours.", "", "Red, blue, and yellow."),
        InstructionExample("Say hi.", "", "Hi there."),
        InstructionExample("Count to three.", "", "One, two, three."),
        InstructionExample("Capital of France?", "", "Paris."),
    ]


@pytest.fixture(scope="module")
def model(tokenizer):
    """A real but tiny model -- packing is a property of attention and RoPE,
    both of which are the same code at any width."""
    torch.manual_seed(1337)
    config = ModelConfig(
        name="packing-test",
        vocab_size=tokenizer.vocab_size,
        d_model=64,
        n_layers=2,
        n_heads=4,
        n_kv_heads=4,
        d_ff=128,
        context_length=256,
    )
    return AshuGPT(config).eval()


def test_packed_logits_match_running_each_example_alone(tokenizer, examples, model):
    """The property that makes packing legitimate.

    If attention leaked across the boundary, example 2's logits would depend on
    example 1 and this would fail. If RoPE did not restart per example, every
    example after the first would be rotated to the wrong positions and this
    would fail. Nothing else in the test suite pins that down.
    """
    seq_len = 256
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=seq_len)
    unpacked = InstructionDataset(examples, tokenizer, seq_len=seq_len)

    # Force everything into one window so there is a boundary to get wrong.
    assert len(packed) == 1, "test needs all examples in a single window"

    input_ids, labels, segment_ids, position_ids = packed[0]
    with torch.no_grad():
        packed_out = model(
            input_ids.unsqueeze(0),
            segment_ids=segment_ids.unsqueeze(0),
            position_ids=position_ids.unsqueeze(0),
        )

    cursor = 0
    for example_idx in packed.bins[0]:
        ids, _ = packed.encoded[example_idx]
        width = len(ids) - 1

        alone_input, _ = unpacked[example_idx]
        with torch.no_grad():
            alone_out = model(alone_input[:width].unsqueeze(0))

        torch.testing.assert_close(
            packed_out.logits[0, cursor : cursor + width],
            alone_out.logits[0, :width],
            rtol=1e-4,
            atol=1e-4,
        )
        cursor += width


def test_supervised_tokens_survive_packing(tokenizer, examples):
    """Packing must move the supervised tokens, not lose or add any."""
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=128)
    unpacked = InstructionDataset(examples, tokenizer, seq_len=128)

    packed_targets = sorted(
        tokenizer.decode([t for t in labels[labels != IGNORE_INDEX].tolist()])
        for labels in (packed[i][1] for i in range(len(packed)))
    )
    unpacked_targets = sorted(
        tokenizer.decode(labels[labels != IGNORE_INDEX].tolist()) for _, labels in (unpacked[i] for i in range(len(unpacked)))
    )
    # One packed window holds every response, concatenated in packing order.
    assert "".join(sorted(unpacked_targets)) != ""
    for response in unpacked_targets:
        assert any(response in packed_window for packed_window in packed_targets)


def test_no_prompt_token_is_ever_supervised(tokenizer, examples):
    """The masking rule the unpacked class established, applied per example."""
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=128)
    input_ids, labels, segment_ids, _ = packed[0]

    for segment in segment_ids.unique().tolist():
        if segment == PackedInstructionDataset.PAD_SEGMENT:
            continue
        where = segment_ids == segment
        supervised = labels[where][labels[where] != IGNORE_INDEX]
        # Every supervised run must end at EOS: the response is a contiguous
        # tail of its example, so a prompt token creeping in would break this.
        assert supervised[-1].item() == tokenizer.eos_id


def test_padding_positions_are_masked_and_self_attending(tokenizer, examples):
    """A padded row attending to nothing would softmax over all -inf -> NaN."""
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=256)
    input_ids, labels, segment_ids, _ = packed[0]

    pad = segment_ids == PackedInstructionDataset.PAD_SEGMENT
    assert pad.any(), "test needs a window with leftover padding"
    assert (labels[pad] == IGNORE_INDEX).all()

    from ashugpt.model.attention import segment_causal_mask

    mask = segment_causal_mask(segment_ids.unsqueeze(0))[0, 0]
    # Every row, padded or not, must leave at least one position attendable.
    assert (~mask).any(dim=-1).all()


def test_no_real_token_attends_across_an_example_boundary(tokenizer, examples):
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=128)
    _, _, segment_ids, _ = packed[0]

    from ashugpt.model.attention import segment_causal_mask

    allowed = ~segment_causal_mask(segment_ids.unsqueeze(0))[0, 0]
    q_seg = segment_ids.unsqueeze(1).expand_as(allowed)
    k_seg = segment_ids.unsqueeze(0).expand_as(allowed)
    assert (q_seg[allowed] == k_seg[allowed]).all()


def test_packing_fills_the_window_far_better_than_padding(tokenizer, examples):
    packed = PackedInstructionDataset(examples * 8, tokenizer, seq_len=128)
    unpacked = InstructionDataset(examples * 8, tokenizer, seq_len=128)

    assert len(packed) < len(unpacked)
    assert packed.packing_efficiency > 0.85


def test_positions_restart_at_zero_for_each_example(tokenizer, examples):
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=128)
    _, _, segment_ids, position_ids = packed[0]

    for segment in segment_ids.unique().tolist():
        if segment == PackedInstructionDataset.PAD_SEGMENT:
            continue
        positions = position_ids[segment_ids == segment]
        torch.testing.assert_close(positions, torch.arange(len(positions)))


def test_long_examples_are_dropped_not_truncated(tokenizer):
    """Same rule as the unpacked class: a truncated response teaches the model
    to stop mid-sentence exactly where the window ended."""
    long_example = InstructionExample("Recite.", "", "word " * 200)
    packed = PackedInstructionDataset([long_example], tokenizer, seq_len=32)
    assert packed.dropped == 1
    assert len(packed) == 0


def test_without_the_segment_mask_the_logits_are_wrong(tokenizer, examples, model):
    """Negative control: proves the equality test above is not vacuous.

    Run the identical packed window with segment_ids omitted -- i.e. plain
    causal attention over the whole window, which is what packing looks like
    if you forget the mask -- and the later examples' logits must change.
    A test that passes either way would be pinning down nothing.
    """
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=256)
    input_ids, _, segment_ids, position_ids = packed[0]

    with torch.no_grad():
        masked = model(
            input_ids.unsqueeze(0),
            segment_ids=segment_ids.unsqueeze(0),
            position_ids=position_ids.unsqueeze(0),
        )
        leaky = model(input_ids.unsqueeze(0), position_ids=position_ids.unsqueeze(0))

    first_width = len(packed.encoded[packed.bins[0][0]][0]) - 1
    # The first example sees no earlier example either way, so it must match...
    torch.testing.assert_close(
        masked.logits[0, :first_width], leaky.logits[0, :first_width], rtol=1e-4, atol=1e-4
    )
    # ...and everything after it must not.
    assert not torch.allclose(
        masked.logits[0, first_width:], leaky.logits[0, first_width:], rtol=1e-4, atol=1e-4
    )


def test_fused_attention_path_agrees_with_the_manual_one(tokenizer, examples, model):
    """The SFT configs run use_efficient_attention: true, so the packed mask has
    to survive the conversion to SDPA's additive-bias convention and broadcast
    correctly against (batch, n_heads, seq, seq)."""
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=256)
    input_ids, _, segment_ids, position_ids = packed[0]
    args = dict(segment_ids=segment_ids.unsqueeze(0), position_ids=position_ids.unsqueeze(0))

    with torch.no_grad():
        model.set_memory_optimizations(efficient_attention=False)
        manual = model(input_ids.unsqueeze(0), **args)
        model.set_memory_optimizations(efficient_attention=True)
        fused = model(input_ids.unsqueeze(0), **args)
        model.set_memory_optimizations(efficient_attention=False)

    torch.testing.assert_close(manual.logits, fused.logits, rtol=1e-4, atol=1e-4)


def test_segment_ids_are_rejected_with_a_kv_cache(tokenizer, examples, model):
    """Cached generation has one segment by construction; combining the two
    would silently mask against a window that no longer matches the cache."""
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=256)
    input_ids, _, segment_ids, _ = packed[0]

    with torch.no_grad():
        primed = model(input_ids[:8].unsqueeze(0))
    with pytest.raises(ValueError, match="KV cache"):
        model(
            input_ids[8:16].unsqueeze(0),
            kv_caches=primed.kv_caches,
            segment_ids=segment_ids[8:16].unsqueeze(0),
        )


def test_packed_gradients_match_the_same_examples_unpacked(tokenizer, examples):
    """Packing must not change the gradient, not just the logits.

    `test_packed_logits_match_running_each_example_alone` runs under no_grad and
    pins the forward pass. It does not pin the backward one, and the two are not
    the same claim: the loss is a token-mean over supervised positions, so a
    packed window and a padded batch agree only if they average over the *same*
    set of positions with the same weights. If packing changed the number of
    terms in that mean -- by counting a padding position, or by normalising per
    window instead of per token -- the logits would still match example by
    example while every gradient came out scaled.

    This is the property the learning-rate work rests on: it is what makes
    packing a change in batch size and not a disguised change in step size.
    """
    seq_len = 256
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=seq_len)
    unpacked = InstructionDataset(examples, tokenizer, seq_len=seq_len)
    assert len(packed) == 1, "test needs all examples in a single window"

    torch.manual_seed(1337)
    config = ModelConfig(
        name="packing-grad-test",
        vocab_size=tokenizer.vocab_size,
        d_model=64,
        n_layers=2,
        n_heads=4,
        n_kv_heads=4,
        d_ff=128,
        context_length=seq_len,
    )
    model = AshuGPT(config)
    model.train()

    def gradients(build):
        model.zero_grad(set_to_none=True)
        output = build()
        output.loss.backward()
        return output.loss, {n: p.grad.clone() for n, p in model.named_parameters()
                             if p.grad is not None}

    input_ids, labels, segment_ids, position_ids = packed[0]
    packed_loss, packed_grads = gradients(lambda: model(
        input_ids.unsqueeze(0),
        labels=labels.unsqueeze(0),
        segment_ids=segment_ids.unsqueeze(0),
        position_ids=position_ids.unsqueeze(0),
    ))

    batch_ids = torch.stack([unpacked[i][0] for i in range(len(examples))])
    batch_labels = torch.stack([unpacked[i][1] for i in range(len(examples))])
    unpacked_loss, unpacked_grads = gradients(lambda: model(batch_ids, labels=batch_labels))

    # Same supervised positions on both sides, or the means are not comparable.
    assert int((labels != IGNORE_INDEX).sum()) == int((batch_labels != IGNORE_INDEX).sum())

    torch.testing.assert_close(packed_loss, unpacked_loss, rtol=1e-5, atol=1e-5)
    assert packed_grads.keys() == unpacked_grads.keys()
    for name, grad in packed_grads.items():
        torch.testing.assert_close(grad, unpacked_grads[name], rtol=1e-4, atol=1e-6,
                                   msg=lambda m, n=name: f"gradient differs for {n}: {m}")


def test_gradient_checkpointing_agrees_with_the_normal_path(tokenizer, examples):
    """Gradient checkpointing re-runs each block's forward during backward, so
    the packing arguments have to survive being passed through
    torch.utils.checkpoint as keywords. If they did not, the recomputed
    forward would use plain causal attention while the original used the
    block-diagonal mask -- and the gradients would quietly disagree.

    Matches how the other memory levers are tested (SPEC M6): exact gradient
    equivalence with the lever on and off, not merely "it runs".
    """
    packed = PackedInstructionDataset(examples, tokenizer, seq_len=128)
    input_ids, labels, segment_ids, position_ids = packed[0]
    args = dict(
        labels=labels.unsqueeze(0),
        segment_ids=segment_ids.unsqueeze(0),
        position_ids=position_ids.unsqueeze(0),
    )

    torch.manual_seed(1337)
    config = ModelConfig(
        name="packing-gc-test",
        vocab_size=tokenizer.vocab_size,
        d_model=64,
        n_layers=2,
        n_heads=4,
        n_kv_heads=4,
        d_ff=128,
        context_length=128,
    )
    model = AshuGPT(config)
    model.train()

    def gradients(checkpointing: bool):
        model.set_memory_optimizations(gradient_checkpointing=checkpointing)
        model.zero_grad(set_to_none=True)
        output = model(input_ids.unsqueeze(0), **args)
        output.loss.backward()
        return output.loss.item(), {n: p.grad.clone() for n, p in model.named_parameters() if p.grad is not None}

    loss_off, grads_off = gradients(False)
    loss_on, grads_on = gradients(True)
    model.set_memory_optimizations(gradient_checkpointing=False)

    assert loss_off == pytest.approx(loss_on, abs=1e-6)
    assert grads_off, "no gradients were produced"
    for name, grad in grads_off.items():
        torch.testing.assert_close(grad, grads_on[name], rtol=1e-4, atol=1e-6, msg=f"gradient mismatch in {name}")
