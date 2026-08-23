"""Unit tests for autoregressive generation: sampling strategies + the
decoding loop's control flow (EOS handling, max length, batching)."""

from types import SimpleNamespace

import pytest
import torch
import torch.nn as nn

from ashugpt.config import ModelConfig
from ashugpt.inference.generate import (
    _filter_top_k,
    _filter_top_p,
    generate,
    generate_stream,
    generate_text,
    sample_next_token,
)
from ashugpt.model.gpt import AshuGPT, GPTOutput
from ashugpt.tokenizer import BPETokenizer


# ============================================================
# sample_next_token / filters
# ============================================================


def test_greedy_is_deterministic_argmax() -> None:
    logits = torch.tensor([[1.0, 5.0, 2.0, 0.0]])
    for seed in (0, 1, 2):
        torch.manual_seed(seed)
        assert sample_next_token(logits, temperature=0.0).item() == 1


def test_negative_temperature_rejected() -> None:
    with pytest.raises(ValueError):
        sample_next_token(torch.randn(1, 5), temperature=-0.5)


def test_low_temperature_almost_always_matches_greedy() -> None:
    logits = torch.tensor([[1.0, 5.0, 2.0, 0.0]])
    torch.manual_seed(0)
    picks = [sample_next_token(logits, temperature=0.05).item() for _ in range(200)]
    assert picks.count(1) / len(picks) > 0.95  # near-greedy: token 1 dominates


def test_high_temperature_increases_diversity() -> None:
    logits = torch.tensor([[1.0, 1.0, 1.0, 1.0, 1.0]])  # uniform-ish logits
    torch.manual_seed(0)
    low_temp_picks = {sample_next_token(logits, temperature=0.1).item() for _ in range(100)}
    torch.manual_seed(0)
    high_temp_picks = {sample_next_token(logits, temperature=2.0).item() for _ in range(100)}
    assert len(high_temp_picks) >= len(low_temp_picks)


def test_top_k_filter_keeps_exactly_k_candidates() -> None:
    logits = torch.tensor([[5.0, 3.0, 1.0, 4.0, 2.0]])
    filtered = _filter_top_k(logits, top_k=2)
    finite = torch.isfinite(filtered)
    assert finite.sum().item() == 2
    assert finite[0, 0] and finite[0, 3]  # the two largest: 5.0 (idx 0) and 4.0 (idx 3)


def test_top_k_clamped_to_vocab_size() -> None:
    logits = torch.randn(1, 5)
    filtered = _filter_top_k(logits, top_k=1000)
    assert torch.isfinite(filtered).all()


def test_top_k_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        _filter_top_k(torch.randn(1, 5), top_k=0)


def test_top_k_sampling_never_leaves_the_allowed_set() -> None:
    logits = torch.tensor([[5.0, 3.0, 1.0, 4.0, 2.0]])
    torch.manual_seed(0)
    picks = {sample_next_token(logits, temperature=1.0, top_k=2).item() for _ in range(200)}
    assert picks <= {0, 3}  # only indices 0 (5.0) and 3 (4.0) are in the top-2


def test_top_p_filter_matches_hand_computed_nucleus() -> None:
    # probs ~= [0.5, 0.25, 0.125, 0.0625, 0.0625]; cumulative crosses 0.9 at
    # index 3 (0.9375), so indices 0-3 survive and index 4 is dropped.
    logits = torch.log(torch.tensor([[0.50, 0.25, 0.125, 0.0625, 0.0625]]))
    filtered = _filter_top_p(logits, top_p=0.9)
    assert torch.isfinite(filtered[0, :4]).all()
    assert filtered[0, 4] == float("-inf")


def test_top_p_always_keeps_the_top_token_even_if_below_threshold() -> None:
    logits = torch.tensor([[10.0, 0.0, 0.0]])  # top token has probability ~1.0 already
    filtered = _filter_top_p(logits, top_p=0.01)  # an absurdly small nucleus
    assert torch.isfinite(filtered[0, 0])  # must never fully mask a row


def test_top_p_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        _filter_top_p(torch.randn(1, 5), top_p=0.0)
    with pytest.raises(ValueError):
        _filter_top_p(torch.randn(1, 5), top_p=1.5)


def test_top_p_sampling_never_leaves_the_nucleus() -> None:
    logits = torch.log(torch.tensor([[0.50, 0.25, 0.125, 0.0625, 0.0625]]))
    torch.manual_seed(0)
    picks = {sample_next_token(logits, temperature=1.0, top_p=0.9).item() for _ in range(200)}
    assert picks <= {0, 1, 2, 3}


def test_combining_top_k_and_top_p_does_not_crash() -> None:
    logits = torch.randn(4, 50)
    torch.manual_seed(0)
    tokens = sample_next_token(logits, temperature=0.8, top_k=10, top_p=0.9)
    assert tokens.shape == (4,)
    assert torch.isfinite(tokens.float()).all()


def test_extreme_filtering_never_produces_nan_probabilities() -> None:
    logits = torch.randn(3, 20)
    torch.manual_seed(0)
    tokens = sample_next_token(logits, temperature=0.5, top_k=1, top_p=0.001)
    assert tokens.shape == (3,)
    assert (tokens >= 0).all() and (tokens < 20).all()


# ============================================================
# generate(): control flow (EOS, max length, batching)
# ============================================================


class _ConstantLogitsModel(nn.Module):
    """Always predicts `next_token_id` at every position, regardless of
    input -- isolates generate()'s loop/EOS/batching logic from a real
    model's unpredictable output."""

    def __init__(self, vocab_size: int, next_token_id: int, context_length: int = 1000) -> None:
        super().__init__()
        self.config = SimpleNamespace(context_length=context_length)
        self.vocab_size = vocab_size
        self.next_token_id = next_token_id

    def forward(self, input_ids: torch.Tensor, **_kwargs) -> GPTOutput:
        batch, seq_len = input_ids.shape
        logits = torch.full((batch, seq_len, self.vocab_size), -10.0)
        logits[:, :, self.next_token_id] = 10.0
        return GPTOutput(logits=logits)


class _TwoCandidateModel(nn.Module):
    """Prefers `first_id`, with `second_id` a close runner-up.

    _ConstantLogitsModel separates its favourite from the rest by 20 logits,
    which no sane repetition penalty can cross -- 10.0 / 3.0 is still far
    above -10.0. Testing that a penalty changes the *chosen* token needs a
    runner-up close enough to overtake, which is also what the real failure
    looks like: a loop is a model picking a token it only narrowly prefers.
    """

    def __init__(self, vocab_size: int, first_id: int, second_id: int, context_length: int = 1000) -> None:
        super().__init__()
        self.config = SimpleNamespace(context_length=context_length)
        self.vocab_size = vocab_size
        self.first_id = first_id
        self.second_id = second_id

    def forward(self, input_ids: torch.Tensor, **_kwargs) -> GPTOutput:
        batch, seq_len = input_ids.shape
        logits = torch.full((batch, seq_len, self.vocab_size), -10.0)
        logits[:, :, self.first_id] = 5.0
        logits[:, :, self.second_id] = 4.0
        return GPTOutput(logits=logits)


class _RowDependentModel(nn.Module):
    """Row 0 always predicts eos_id; every other row always predicts
    other_id -- lets us test that one finished row doesn't stop the rest
    of the batch, and gets padded with eos_id while the rest keep going."""

    def __init__(self, vocab_size: int, eos_id: int, other_id: int, context_length: int = 1000) -> None:
        super().__init__()
        self.config = SimpleNamespace(context_length=context_length)
        self.vocab_size = vocab_size
        self.eos_id = eos_id
        self.other_id = other_id

    def forward(self, input_ids: torch.Tensor, **_kwargs) -> GPTOutput:
        batch, seq_len = input_ids.shape
        logits = torch.full((batch, seq_len, self.vocab_size), -10.0)
        logits[0, :, self.eos_id] = 10.0
        if batch > 1:
            logits[1:, :, self.other_id] = 10.0
        return GPTOutput(logits=logits)


def test_generation_respects_max_new_tokens_without_eos() -> None:
    model = _ConstantLogitsModel(vocab_size=20, next_token_id=5)
    input_ids = torch.tensor([[1, 2, 3]])
    out = generate(model, input_ids, max_new_tokens=10, temperature=0.0, eos_id=None)
    assert out.shape == (1, 3 + 10)
    assert (out[:, 3:] == 5).all()


def test_generation_stops_early_when_all_rows_finish() -> None:
    model = _ConstantLogitsModel(vocab_size=20, next_token_id=7)
    input_ids = torch.tensor([[1, 2, 3], [4, 5, 6]])
    out = generate(model, input_ids, max_new_tokens=10, temperature=0.0, eos_id=7)
    assert out.shape == (2, 3 + 1)  # loop broke after the very first generated token


def test_unfinished_row_does_not_get_cut_short_by_a_finished_one() -> None:
    eos_id, other_id = 0, 1
    model = _RowDependentModel(vocab_size=20, eos_id=eos_id, other_id=other_id)
    input_ids = torch.tensor([[5, 6], [5, 6]])
    out = generate(model, input_ids, max_new_tokens=4, temperature=0.0, eos_id=eos_id)

    assert out.shape == (2, 2 + 4)  # row 1 never finishes, so all 4 steps run
    assert (out[0, 2:] == eos_id).all()  # row 0: finished immediately, padded with eos_id throughout
    assert (out[1, 2:] == other_id).all()  # row 1: kept generating normally the whole time


def test_context_length_overflow_raises_before_running_the_model() -> None:
    model = _ConstantLogitsModel(vocab_size=20, next_token_id=5, context_length=10)
    input_ids = torch.tensor([[1, 2, 3, 4, 5]])
    with pytest.raises(ValueError):
        generate(model, input_ids, max_new_tokens=10)  # 5 + 10 > context_length=10


def test_generate_restores_original_training_mode() -> None:
    model = _ConstantLogitsModel(vocab_size=20, next_token_id=5)
    model.train()
    generate(model, torch.tensor([[1, 2]]), max_new_tokens=3, temperature=0.0)
    assert model.training

    model.eval()
    generate(model, torch.tensor([[1, 2]]), max_new_tokens=3, temperature=0.0)
    assert not model.training


# ============================================================
# generate() / generate_text() against a real (untrained) AshuGPT model
# ============================================================


def small_model_config(vocab_size: int = 64) -> ModelConfig:
    return ModelConfig(
        name="test", vocab_size=vocab_size, d_model=16, n_layers=2, n_heads=2, n_kv_heads=2, d_ff=32, context_length=64
    )


@pytest.mark.parametrize("batch_size,prompt_len", [(1, 3), (2, 5), (4, 2)])
def test_generate_handles_various_batch_sizes(batch_size: int, prompt_len: int) -> None:
    torch.manual_seed(0)
    config = small_model_config()
    model = AshuGPT(config)
    input_ids = torch.randint(0, config.vocab_size, (batch_size, prompt_len))

    out = generate(model, input_ids, max_new_tokens=5, temperature=1.0, top_k=10)
    assert out.shape == (batch_size, prompt_len + 5)
    assert torch.equal(out[:, :prompt_len], input_ids)  # the prompt itself is untouched


def test_greedy_generation_is_reproducible_regardless_of_seed() -> None:
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    model.eval()
    input_ids = torch.randint(0, 64, (1, 4))

    torch.manual_seed(1)
    out_a = generate(model, input_ids, max_new_tokens=8, temperature=0.0)
    torch.manual_seed(999)
    out_b = generate(model, input_ids, max_new_tokens=8, temperature=0.0)
    assert torch.equal(out_a, out_b)  # greedy decoding doesn't touch the RNG at all


def test_sampling_with_same_seed_is_reproducible() -> None:
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    model.eval()
    input_ids = torch.randint(0, 64, (1, 4))

    torch.manual_seed(42)
    out_a = generate(model, input_ids, max_new_tokens=8, temperature=0.8, top_k=20)
    torch.manual_seed(42)
    out_b = generate(model, input_ids, max_new_tokens=8, temperature=0.8, top_k=20)
    assert torch.equal(out_a, out_b)


def test_generate_text_roundtrips_through_the_tokenizer(tmp_path) -> None:
    corpus = "the quick fox jumps over the lazy dog. " * 20
    tokenizer = BPETokenizer.train(corpus, vocab_size=300)

    torch.manual_seed(0)
    config = small_model_config(vocab_size=tokenizer.vocab_size)
    model = AshuGPT(config)

    text = generate_text(model, tokenizer, prompt="the quick", max_new_tokens=10, temperature=0.8, top_k=20)
    assert isinstance(text, str)
    for special in ("<pad>", "<bos>", "<eos>", "<unk>"):
        assert special not in text  # decode() strips special tokens by default


# ============================================================
# KV cache: use_cache=True must match use_cache=False exactly
# ============================================================


@pytest.mark.parametrize("batch_size,prompt_len", [(1, 3), (2, 5), (3, 1)])
def test_cached_and_uncached_greedy_generation_match_exactly(batch_size: int, prompt_len: int) -> None:
    # Greedy decoding is fully deterministic (no RNG involved), so this is
    # the strictest possible check: if caching changed *anything* the
    # model computes, an argmax somewhere would eventually pick a
    # different token and the two token sequences would diverge.
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    model.eval()
    input_ids = torch.randint(0, 64, (batch_size, prompt_len))

    out_cached = generate(model, input_ids, max_new_tokens=12, temperature=0.0, use_cache=True)
    out_uncached = generate(model, input_ids, max_new_tokens=12, temperature=0.0, use_cache=False)
    assert torch.equal(out_cached, out_uncached)


def test_cached_and_uncached_generation_match_with_eos_at_different_times() -> None:
    # eos_id chosen so different rows in the batch are likely to finish at
    # different steps -- exercises the "some rows finished, some still
    # generating" path identically in both code paths.
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    model.eval()
    input_ids = torch.randint(0, 64, (4, 4))

    out_cached = generate(model, input_ids, max_new_tokens=15, temperature=0.0, eos_id=3, use_cache=True)
    out_uncached = generate(model, input_ids, max_new_tokens=15, temperature=0.0, eos_id=3, use_cache=False)
    assert torch.equal(out_cached, out_uncached)


def test_cached_and_uncached_sampling_match_given_the_same_seed() -> None:
    # Caching only changes *how* logits are computed, not the sampling
    # procedure or how many random draws it consumes -- with the same
    # seed and (numerically near-identical) logits, both paths should pick
    # the same tokens.
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    model.eval()
    input_ids = torch.randint(0, 64, (3, 5))

    torch.manual_seed(123)
    out_cached = generate(model, input_ids, max_new_tokens=15, temperature=0.9, top_k=20, use_cache=True)
    torch.manual_seed(123)
    out_uncached = generate(model, input_ids, max_new_tokens=15, temperature=0.9, top_k=20, use_cache=False)
    assert torch.equal(out_cached, out_uncached)


def test_cache_grows_by_one_position_per_layer_per_step() -> None:
    torch.manual_seed(0)
    config = small_model_config()
    model = AshuGPT(config)
    model.eval()
    prompt = torch.randint(0, config.vocab_size, (2, 6))

    with torch.no_grad():
        output = model(prompt, kv_caches=None, position_offset=0)
        for layer_idx, (k, v) in enumerate(output.kv_caches):
            expected_head_dim = config.d_model // config.n_heads
            assert k.shape == (2, config.n_heads, 6, expected_head_dim), f"layer {layer_idx} key cache shape"
            assert v.shape == (2, config.n_heads, 6, expected_head_dim), f"layer {layer_idx} value cache shape"

        next_token = torch.randint(0, config.vocab_size, (2, 1))
        output = model(next_token, kv_caches=output.kv_caches, position_offset=6)
        for k, v in output.kv_caches:
            assert k.shape[2] == 7  # grew from 6 cached + 1 new
            assert v.shape[2] == 7


# ============================================================
# generate_stream -- the loop generate() is built on
# ============================================================


def test_stream_and_generate_produce_the_same_tokens() -> None:
    # generate() is generate_stream() concatenated, so this is really a
    # check that the refactor kept one implementation rather than two: any
    # divergence in sampling, EOS handling or cache bookkeeping between the
    # streaming and collected paths shows up here as different tokens.
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    model.eval()
    input_ids = torch.randint(0, 64, (2, 4))

    torch.manual_seed(99)
    collected = generate(model, input_ids, max_new_tokens=10, temperature=0.9, top_k=8)

    torch.manual_seed(99)
    streamed = torch.cat(
        [input_ids]
        + [
            step.unsqueeze(1)
            for step in generate_stream(model, input_ids, max_new_tokens=10, temperature=0.9, top_k=8)
        ],
        dim=1,
    )
    assert torch.equal(collected, streamed)


def test_stream_yields_one_step_at_a_time() -> None:
    model = _ConstantLogitsModel(vocab_size=20, next_token_id=5)
    steps = list(generate_stream(model, torch.tensor([[1, 2, 3]]), max_new_tokens=4, temperature=0.0))
    assert len(steps) == 4
    assert all(step.shape == (1,) for step in steps)
    assert all(step.item() == 5 for step in steps)


def test_stream_stops_early_when_all_rows_hit_eos() -> None:
    model = _ConstantLogitsModel(vocab_size=20, next_token_id=7)
    steps = list(generate_stream(model, torch.tensor([[1, 2]]), max_new_tokens=50, temperature=0.0, eos_id=7))
    assert len(steps) == 1  # the very first sampled token is EOS


def test_stream_runs_with_gradients_disabled() -> None:
    """A streaming generator that built an autograd graph would hold every
    intermediate activation alive for the whole generation -- the exact
    failure the @torch.no_grad() decorator on a *generator function* is
    there to prevent, and one that is easy to get wrong because the
    decorator has to re-enter the context at every resume rather than wrap
    a single call."""
    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    observed = []
    original_forward = model.forward

    def recording_forward(*args, **kwargs):
        observed.append(torch.is_grad_enabled())
        return original_forward(*args, **kwargs)

    model.forward = recording_forward
    list(generate_stream(model, torch.tensor([[1, 2, 3]]), max_new_tokens=3, temperature=0.0))

    assert observed and not any(observed)


def test_abandoning_a_stream_restores_training_mode() -> None:
    # A browser closing a streaming connection abandons the generator
    # part-way through, which raises GeneratorExit at the yield rather than
    # running off the end of the function.
    model = _ConstantLogitsModel(vocab_size=20, next_token_id=5)
    model.train()
    stream = generate_stream(model, torch.tensor([[1, 2]]), max_new_tokens=20, temperature=0.0)
    next(stream)
    stream.close()
    assert model.training


def test_stream_validates_context_length_before_yielding_anything() -> None:
    # The check has to happen eagerly, not at the first next() -- a caller
    # that builds a streaming HTTP response before pulling the first token
    # would otherwise have already sent 200 OK by the time it fails.
    model = _ConstantLogitsModel(vocab_size=20, next_token_id=5, context_length=10)
    with pytest.raises(ValueError, match="context_length"):
        generate_stream(model, torch.tensor([[1] * 8]), max_new_tokens=5, temperature=0.0)

# ---- repetition penalty ----


def test_repetition_penalty_of_one_changes_nothing():
    """The default has to be exactly the identity.

    Every entry point defaults to 1.0, so if this were approximate the
    penalty would silently alter every generation that never asked for it.
    """
    from ashugpt.inference.generate import apply_repetition_penalty

    logits = torch.tensor([[1.0, -2.0, 3.0, -4.0]])
    seen = torch.tensor([[0, 2]])
    assert torch.equal(apply_repetition_penalty(logits, seen, 1.0), logits)


def test_repetition_penalty_lowers_seen_tokens_of_either_sign():
    """A positive logit is divided and a negative one multiplied.

    The asymmetry is the point: dividing a negative logit would move it
    toward zero and make an already-disfavoured token *more* likely, which
    is the opposite of a penalty. Both branches must move the score down.
    """
    from ashugpt.inference.generate import apply_repetition_penalty

    logits = torch.tensor([[2.0, -2.0, 5.0]])
    seen = torch.tensor([[0, 1]])  # index 2 is untouched
    out = apply_repetition_penalty(logits, seen, 2.0)

    assert out[0, 0] == pytest.approx(1.0)   # 2.0 / 2
    assert out[0, 1] == pytest.approx(-4.0)  # -2.0 * 2
    assert out[0, 2] == pytest.approx(5.0)   # never generated, untouched
    assert (out[0, :2] < logits[0, :2]).all()


def test_repetition_penalty_is_per_row():
    """seen_ids is (batch, context) and each row penalises its own tokens."""
    from ashugpt.inference.generate import apply_repetition_penalty

    logits = torch.tensor([[4.0, 4.0], [4.0, 4.0]])
    seen = torch.tensor([[0], [1]])
    out = apply_repetition_penalty(logits, seen, 2.0)
    assert out[0].tolist() == [2.0, 4.0]
    assert out[1].tolist() == [4.0, 2.0]


def test_repetition_penalty_rejects_values_below_one():
    """Below 1.0 this becomes a repetition *bonus*, which nothing wants."""
    from ashugpt.inference.generate import apply_repetition_penalty

    with pytest.raises(ValueError, match="repetition_penalty"):
        apply_repetition_penalty(torch.zeros(1, 4), torch.tensor([[0]]), 0.9)


def test_repetition_penalty_actually_suppresses_a_repeat():
    """End to end: a model that only ever wants one token stops repeating it.

    Greedy decoding on a model whose logits are constant emits the same id
    forever. With a penalty it cannot, which is the behaviour loop rate
    measures.
    """
    from ashugpt.inference.generate import generate

    model = _TwoCandidateModel(vocab_size=8, first_id=3, second_id=2)
    prompt = torch.tensor([[0]])

    unpenalised = generate(model, prompt, max_new_tokens=4, temperature=0.0, use_cache=False)
    assert unpenalised[0, 1:].tolist() == [3, 3, 3, 3], "greedy on constant logits repeats forever"

    penalised = generate(
        model, prompt, max_new_tokens=4, temperature=0.0, repetition_penalty=2.0, use_cache=False
    )
    assert penalised[0, 1:].tolist() != [3, 3, 3, 3]
    assert len(set(penalised[0, 1:].tolist())) > 1, "the penalty did not break the loop"


def test_repetition_penalty_is_identical_with_and_without_the_cache():
    """The cached path tracks `seen` separately, so it can drift from the other.

    Cache-equivalence is the property the rest of this file guards for
    sampling; the penalty is the first thing here that needs the token ids
    rather than the K/V, so it gets its own check.
    """
    from ashugpt.inference.generate import generate

    torch.manual_seed(0)
    model = AshuGPT(small_model_config())
    model.eval()
    prompt = torch.randint(0, 64, (2, 4))

    cached = generate(model, prompt, max_new_tokens=8, temperature=0.0, repetition_penalty=1.6, use_cache=True)
    uncached = generate(model, prompt, max_new_tokens=8, temperature=0.0, repetition_penalty=1.6, use_cache=False)
    assert torch.equal(cached, uncached)
