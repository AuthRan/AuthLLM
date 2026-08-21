"""DPO: the loss, the frozen reference, and the wrapper that hides both.

Every failure mode here is silent. A DPO run with a reference that is not
frozen still produces a falling loss and a rising accuracy -- against a
moving baseline, which is no longer the objective anyone derived. A run
that averages log-probabilities instead of summing them trains a
length-normalized variant and only says so through slowly shortening
answers. So these tests pin the arithmetic against values computed by hand
rather than against a previous run's output.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import torch
import torch.nn as nn

from ashugpt.config import ModelConfig, TrainConfig
from ashugpt.data.instruction import IGNORE_INDEX
from ashugpt.data.preference import PreferenceDataset, PreferenceExample
from ashugpt.model import AshuGPT
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer
from ashugpt.training.dpo import DPOModel, dpo_loss, sequence_logprobs
from ashugpt.training.trainer import train

LOG2 = math.log(2)


@pytest.fixture(scope="module")
def tokenizer():
    return TiktokenBPETokenizer()


def _model_config(vocab_size: int = 64, context_length: int = 32) -> ModelConfig:
    return ModelConfig(
        name="dpo-test",
        vocab_size=vocab_size,
        d_model=32,
        n_layers=2,
        n_heads=2,
        n_kv_heads=2,
        d_ff=64,
        context_length=context_length,
    )


# ---- sequence_logprobs ----


def test_sequence_logprobs_sums_only_supervised_positions() -> None:
    """The quantity in the objective is log pi(y|x) for the response -- a sum
    over response tokens, with the prompt and the padding contributing nothing."""
    torch.manual_seed(0)
    logits = torch.randn(1, 4, 10)
    labels = torch.tensor([[IGNORE_INDEX, 3, 7, IGNORE_INDEX]])

    log_probs = torch.log_softmax(logits.float(), dim=-1)
    expected = log_probs[0, 1, 3] + log_probs[0, 2, 7]

    assert torch.allclose(sequence_logprobs(logits, labels), expected.unsqueeze(0), atol=1e-6)


def test_sequence_logprobs_is_a_sum_not_a_mean() -> None:
    """Averaging would be a different algorithm (length-normalized DPO), and
    the difference shows up only as a drift toward shorter answers."""
    torch.manual_seed(0)
    logits = torch.randn(1, 6, 10).repeat(2, 1, 1)
    one_token = torch.full((1, 6), IGNORE_INDEX)
    one_token[0, 2] = 5
    three_tokens = one_token.clone()
    three_tokens[0, 3] = 5
    three_tokens[0, 4] = 5

    short, long = sequence_logprobs(logits[:1], one_token), sequence_logprobs(logits[1:], three_tokens)
    # Every term is negative, so more supervised tokens must mean a strictly
    # smaller total. A mean would leave the two within noise of each other.
    assert long.item() < short.item() - 0.5


def test_masked_positions_never_index_the_gather() -> None:
    """IGNORE_INDEX is -100, which is not a valid vocabulary index; the
    implementation substitutes 0 and masks the result afterwards."""
    logits = torch.zeros(1, 3, 5)
    labels = torch.full((1, 3), IGNORE_INDEX)
    assert sequence_logprobs(logits, labels).item() == 0.0


# ---- the loss ----


def test_loss_is_log_two_when_the_policy_is_the_reference() -> None:
    """The state every run starts in: no divergence yet, so both implicit
    rewards are zero, the margin is zero, and -log sigmoid(0) is log 2."""
    logps = torch.tensor([-5.0, -8.0])
    metrics = dpo_loss(logps, logps.roll(1), logps, logps.roll(1), beta=0.1)

    assert metrics.loss.item() == pytest.approx(LOG2, abs=1e-6)
    assert metrics.margin.item() == pytest.approx(0.0, abs=1e-6)


def test_undecided_pairs_score_fifty_percent() -> None:
    """A tie is half a correct ranking. Scoring it strictly would report 0%
    for a policy that has simply not moved yet -- see DPOMetrics.accuracy."""
    logps = torch.tensor([-5.0, -8.0, -2.0])
    metrics = dpo_loss(logps, logps, logps, logps)
    assert metrics.accuracy.item() == pytest.approx(0.5)


def test_loss_falls_when_the_policy_prefers_the_chosen_answer() -> None:
    policy_chosen = torch.tensor([-4.0])  # reference said -5: made more likely
    policy_rejected = torch.tensor([-9.0])  # reference said -8: made less likely
    reference_chosen, reference_rejected = torch.tensor([-5.0]), torch.tensor([-8.0])

    metrics = dpo_loss(policy_chosen, policy_rejected, reference_chosen, reference_rejected, beta=0.1)

    assert metrics.chosen_reward.item() == pytest.approx(0.1)
    assert metrics.rejected_reward.item() == pytest.approx(-0.1)
    assert metrics.loss.item() < LOG2
    assert metrics.accuracy.item() == 1.0


def test_beta_scales_the_implicit_reward() -> None:
    args = (torch.tensor([-4.0]), torch.tensor([-9.0]), torch.tensor([-5.0]), torch.tensor([-8.0]))
    small, large = dpo_loss(*args, beta=0.1), dpo_loss(*args, beta=0.5)

    assert large.margin.item() == pytest.approx(5 * small.margin.item())
    # A larger beta reads the same divergence as a larger margin, so the same
    # policy is already "further along" and the loss is lower.
    assert large.loss.item() < small.loss.item()


def test_loss_stays_finite_on_a_hopeless_pair() -> None:
    """log(sigmoid(x)) underflows to -inf for large negative x and takes the
    gradient with it; softplus(-x) is the same function, computed safely.
    Early training on a hard pair sits exactly in this regime."""
    metrics = dpo_loss(
        torch.tensor([-500.0], requires_grad=True),
        torch.tensor([0.0]),
        torch.tensor([0.0]),
        torch.tensor([0.0]),
        beta=1.0,
    )
    assert torch.isfinite(metrics.loss)
    metrics.loss.backward()


# ---- the wrapper ----


def _pair_batch(batch: int = 2, seq_len: int = 8, vocab_size: int = 64) -> tuple[torch.Tensor, torch.Tensor]:
    generator = torch.Generator().manual_seed(1)
    input_ids = torch.randint(0, vocab_size, (batch, 2, seq_len), generator=generator)
    labels = torch.full((batch, 2, seq_len), IGNORE_INDEX)
    labels[:, :, 4:] = input_ids[:, :, 4:]
    return input_ids, labels


def _dpo_model(seed: int = 0, config: ModelConfig | None = None) -> DPOModel:
    config = config or _model_config()
    torch.manual_seed(seed)
    policy = AshuGPT(config)
    reference = AshuGPT(config)
    reference.load_state_dict(policy.state_dict())
    return DPOModel(policy, reference, beta=0.1)


def test_a_fresh_run_starts_at_exactly_log_two() -> None:
    """Policy and reference are the same weights, so every log-probability
    cancels. Anything else here means the two models did not start equal."""
    model = _dpo_model()
    input_ids, labels = _pair_batch()

    output = model(input_ids, labels=labels)

    assert output.loss.item() == pytest.approx(LOG2, abs=1e-5)
    assert model.last_metrics.accuracy.item() == pytest.approx(0.5)


def test_the_reference_never_trains() -> None:
    model = _dpo_model()
    before = {name: p.clone() for name, p in model.reference.named_parameters()}
    input_ids, labels = _pair_batch()

    optimizer = torch.optim.SGD([p for p in model.parameters() if p.requires_grad], lr=1.0)
    model.train()
    model(input_ids, labels=labels).loss.backward()
    optimizer.step()

    assert not model.reference.training, "train() must leave the reference in eval mode"
    for name, p in model.reference.named_parameters():
        assert torch.equal(p, before[name]), f"reference parameter {name} moved"
        assert p.grad is None


def test_training_raises_the_chosen_answer_above_the_rejected_one() -> None:
    """The whole objective, end to end: after a step, the policy assigns more
    probability to the chosen answer than to the rejected one, relative to
    where it started."""
    model = _dpo_model()
    input_ids, labels = _pair_batch()

    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=1e-2)
    model.train()
    for _ in range(5):
        optimizer.zero_grad()
        model(input_ids, labels=labels).loss.backward()
        optimizer.step()

    output = model(input_ids, labels=labels)
    metrics = model.last_metrics
    assert output.loss.item() < LOG2
    assert metrics.margin.item() > 0
    assert metrics.accuracy.item() == 1.0


def test_the_checkpoint_is_a_plain_model() -> None:
    """A DPO run produces a model, not a model plus its own history. A state
    dict carrying both under prefixed keys could not be loaded by inference,
    evaluation, or a later fine-tune."""
    model = _dpo_model()
    plain = AshuGPT(_model_config())

    state = model.state_dict()
    assert set(state) == set(plain.state_dict())
    plain.load_state_dict(state)  # raises if a key is prefixed or missing


def test_a_batch_that_is_not_pairs_is_refused() -> None:
    model = _dpo_model()
    with pytest.raises(ValueError, match="chosen first"):
        model(torch.zeros(2, 8, dtype=torch.long), labels=torch.zeros(2, 8, dtype=torch.long))
    with pytest.raises(ValueError, match="labels"):
        model(torch.zeros(2, 2, 8, dtype=torch.long))


def test_perplexity_is_not_reported_for_a_ranking_loss() -> None:
    """exp(DPO loss) is a number between 1 and 2 that means nothing. The
    validation log leaves the column empty rather than filling it."""
    from torch.utils.data import DataLoader, TensorDataset

    from ashugpt.eval.perplexity import evaluate

    model = _dpo_model()
    input_ids, labels = _pair_batch()
    loader = DataLoader(TensorDataset(input_ids, labels), batch_size=2)

    metrics = evaluate(model, loader, amp_dtype=None)
    assert "perplexity" not in metrics
    assert metrics["loss"] == pytest.approx(LOG2, abs=1e-5)


def test_dpo_runs_through_the_ordinary_training_loop(tmp_path: Path, tokenizer) -> None:
    """The reason DPOModel exists: `train()` is reused whole, so the LR
    schedule, accumulation, logging, checkpointing and resume cannot drift
    from what every other run in this repo uses."""
    # The prompt template alone is ~40 tokens, so this window is the smallest
    # one that fits a whole example rather than dropping every pair.
    model_config = _model_config(vocab_size=tokenizer.vocab_size, context_length=128)
    train_config = TrainConfig(
        batch_size=2,
        seq_len=128,
        max_steps=2,
        warmup_steps=1,
        max_lr=1e-4,
        min_lr=1e-5,
        log_interval=1,
        eval_interval=2,
        eval_steps=1,
        checkpoint_interval=2,
        amp_dtype="none",
        num_workers=0,
        seed=0,
    )
    examples = [
        PreferenceExample(f"Question {i}?", "", f"A good answer number {i}.", "bad") for i in range(8)
    ]
    dataset = PreferenceDataset(examples, tokenizer, seq_len=train_config.seq_len)
    assert len(dataset) == 8

    history = train(
        _dpo_model(config=model_config),
        dataset,
        dataset,
        train_config,
        model_config=model_config,
        checkpoint_dir=tmp_path,
    )

    # The validation row carries a held-out DPO loss and an empty perplexity
    # column, rather than exp() of a loss that is not a cross-entropy.
    val_rows = [row for row in history if "val_loss" in row]
    assert val_rows and val_rows[0]["val_perplexity"] is None

    # What the run wrote must load as an ordinary model.
    checkpoint = torch.load(tmp_path / "step_2.pt", weights_only=True)
    AshuGPT(model_config).load_state_dict(checkpoint["model_state_dict"])


def test_packing_arguments_are_refused_rather_than_dropped() -> None:
    """Silently ignoring segment_ids would run block-diagonal-masked data under
    a plain causal mask, and the loss curve would not notice."""
    model = _dpo_model()
    input_ids, labels = _pair_batch()
    with pytest.raises(TypeError, match="never packed"):
        model(input_ids, labels=labels, segment_ids=torch.zeros_like(input_ids))


def test_raw_accuracy_scores_the_policy_without_the_reference() -> None:
    """Measured on the model alone, so an SFT checkpoint can be scored with it
    and a before/after table means something."""
    metrics = dpo_loss(
        policy_chosen_logps=torch.tensor([-4.0, -9.0]),
        policy_rejected_logps=torch.tensor([-9.0, -4.0]),
        reference_chosen_logps=torch.tensor([-4.0, -9.0]),
        reference_rejected_logps=torch.tensor([-9.0, -4.0]),
    )
    # No divergence from the reference at all: the DPO accuracy is a coin
    # flip, but the model itself ranks one of the two pairs correctly.
    assert metrics.accuracy.item() == pytest.approx(0.5)
    assert metrics.raw_accuracy.item() == pytest.approx(0.5)

    one_sided = dpo_loss(
        policy_chosen_logps=torch.tensor([-4.0, -4.0]),
        policy_rejected_logps=torch.tensor([-9.0, -9.0]),
        reference_chosen_logps=torch.tensor([-4.0, -4.0]),
        reference_rejected_logps=torch.tensor([-9.0, -9.0]),
    )
    assert one_sided.raw_accuracy.item() == 1.0


def test_length_normalization_undoes_a_pure_length_preference() -> None:
    """A long answer scores lower for being long. A model that has learned
    only that ranks perfectly by summed log-probability and not at all
    per token -- which is the whole reason both numbers are reported."""
    metrics = dpo_loss(
        policy_chosen_logps=torch.tensor([-20.0]),  # 10 tokens at -2.0 each
        policy_rejected_logps=torch.tensor([-40.0]),  # 20 tokens at -2.0 each
        reference_chosen_logps=torch.tensor([-20.0]),
        reference_rejected_logps=torch.tensor([-40.0]),
        chosen_tokens=torch.tensor([10.0]),
        rejected_tokens=torch.tensor([20.0]),
    )
    assert metrics.raw_accuracy.item() == 1.0
    assert metrics.length_normalized_accuracy.item() == pytest.approx(0.5)  # a tie, per token


def test_evaluate_preferences_weights_by_pairs_not_batches() -> None:
    """A short final batch must not count as much as a full one -- otherwise
    the held-out number depends on where the batch boundary happened to fall."""
    from torch.utils.data import DataLoader, TensorDataset

    from ashugpt.eval.preference import evaluate_preferences

    model = _dpo_model()
    input_ids, labels = _pair_batch(batch=3)
    dataset = TensorDataset(input_ids, labels)

    even = evaluate_preferences(model, DataLoader(dataset, batch_size=3), amp_dtype=None)
    split = evaluate_preferences(model, DataLoader(dataset, batch_size=2), amp_dtype=None)  # 2 + 1

    for key, value in even.items():
        if math.isnan(value):
            assert math.isnan(split[key]), key
            continue
        assert split[key] == pytest.approx(value, abs=1e-5), key
    assert even["accuracy"] == pytest.approx(0.5)  # policy is the reference

    # Both sides of every pair here are the same length, so the length split
    # has no pairs to report on. That is nan, not 0% -- "there were none of
    # these" and "it got none of these right" are different statements.
    assert math.isnan(even["raw_accuracy_chosen_shorter"])
    assert math.isnan(even["raw_accuracy_chosen_longer"])
    assert even["fraction_chosen_shorter"] == 0.0


def test_evaluate_preferences_refuses_a_model_without_metrics() -> None:
    from torch.utils.data import DataLoader, TensorDataset

    from ashugpt.eval.preference import evaluate_preferences

    input_ids, labels = _pair_batch()
    loader = DataLoader(TensorDataset(input_ids[:, 0], labels[:, 0]), batch_size=2)
    with pytest.raises(TypeError, match="DPOModel"):
        evaluate_preferences(AshuGPT(_model_config()), loader, amp_dtype=None)


def test_the_length_split_separates_a_length_detector_from_a_ranker() -> None:
    """A model that only prefers shorter answers scores 100% on the pairs
    where the chosen answer is shorter and 0% where it is longer, while its
    average looks like an unremarkable coin flip."""
    from torch.utils.data import DataLoader, TensorDataset

    from ashugpt.eval.preference import evaluate_preferences

    class LengthDetector(nn.Module):
        """Assigns every token the same log-probability, so the longer answer
        always has the lower total -- ranking by length and nothing else."""

        loss_is_cross_entropy = False

        def __init__(self) -> None:
            super().__init__()
            self.marker = nn.Parameter(torch.zeros(1))
            self.last_metrics = None

        def forward(self, input_ids, labels=None, **_):
            n_tokens = (labels != IGNORE_INDEX).sum(dim=-1).float()
            logps = -2.0 * n_tokens  # -2 nats per supervised token, always
            chosen_logps, rejected_logps = logps.unbind(dim=1)
            chosen_tokens, rejected_tokens = n_tokens.unbind(dim=1)
            self.last_metrics = dpo_loss(
                chosen_logps,
                rejected_logps,
                chosen_logps,
                rejected_logps,
                chosen_tokens=chosen_tokens,
                rejected_tokens=rejected_tokens,
            )
            return self.last_metrics

    labels = torch.full((4, 2, 12), IGNORE_INDEX)
    labels[:2, 0, :4] = 1  # two pairs where the chosen answer is shorter
    labels[:2, 1, :8] = 1
    labels[2:, 0, :8] = 1  # two where it is longer
    labels[2:, 1, :4] = 1
    loader = DataLoader(TensorDataset(torch.zeros_like(labels), labels), batch_size=2)

    metrics = evaluate_preferences(LengthDetector(), loader, amp_dtype=None)

    assert metrics["raw_accuracy"] == pytest.approx(0.5)
    assert metrics["raw_accuracy_chosen_shorter"] == 1.0
    assert metrics["raw_accuracy_chosen_longer"] == 0.0
    assert metrics["length_normalized_accuracy"] == pytest.approx(0.5)  # every token scores alike
