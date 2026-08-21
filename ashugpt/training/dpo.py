"""Direct Preference Optimization: learning from "this answer is better".

Supervised fine-tuning (README section 10) shows the model good answers.
It never shows it a bad one, so it has no way to learn what makes the good
one better -- only what good ones look like. Every answer in the training
data is treated as equally worth imitating, including the mediocre ones,
because imitation is the only signal cross-entropy can carry.

Preference tuning uses pairs: one prompt, two answers, and a human's
judgement of which is better. The textbook route is RLHF -- fit a reward
model to the preferences, then optimize the policy against that reward with
PPO, which means running four models and a reinforcement-learning loop.
DPO's contribution is the observation that the optimal policy for that
objective has a closed form, and substituting it back turns the whole thing
into a *classification* loss over pairs, with no reward model and no
sampling:

    L = -log sigmoid( beta * [ (log pi(chosen) - log ref(chosen))
                             - (log pi(rejected) - log ref(rejected)) ] )

Read the bracket as a margin. `log pi(y) - log ref(y)` is how much more
likely the policy has made answer `y` than the frozen reference did -- an
*implicit reward*, never fitted, just measured. The loss pushes that
quantity up for the chosen answer and down for the rejected one, and
sigmoid means it stops caring once the margin is comfortably positive
rather than pushing forever.

The reference model is why this does not simply collapse. Nothing in "make
chosen more likely than rejected" forbids destroying the model on the way
-- assigning both answers near-zero probability satisfies the ranking
perfectly well. Anchoring each term to a frozen copy of where training
started makes the objective about *changes* in likelihood, so drifting far
from the initial model costs something on both sides of the comparison.
That is the same job KL regularization does in RLHF, arrived at by
algebra rather than by adding a penalty term.

`beta` sets how much divergence from the reference is tolerated: small beta
lets the policy move further, large beta keeps it close. 0.1 is the usual
starting point and what this repo's runs use.

## Length normalization, and why it is an option here

Summed log-probabilities are all negative, so a longer answer scores lower
for being longer -- typically another 2-3 nats per token. On this repo's
preference data that dwarfs any difference in content: HH-RLHF's chosen
answers average 80 tokens against the rejected answers' 73, and the
instruction-tuned model ranks the chosen answer first 92.8% of the time
when it is shorter and 8.3% when it is longer. It is a length detector,
and standard DPO left that untouched (README section 10.8).

`length_normalized=True` divides each sequence's log-probability by its own
number of supervised tokens, so every term in the objective is a per-token
average and the length signal cancels. This is a real change of algorithm,
not a scaling detail -- the implicit reward it defines is a different
quantity, and its `beta` does not mean the same thing as the standard
one's, because the margins it produces are ~L times smaller. Runs made
under the two settings are not comparable except through the
reference-free metrics in `ashugpt/eval/preference.py`.

Implementation notes:

- **Log-probabilities are summed over the response, not averaged.** The
  quantity in the loss is log pi(y|x) for a whole sequence, which is a sum.
  Averaging makes the objective length-normalized, which is a different
  (and defensible, and much-debated) algorithm; the failure mode being
  avoided is doing it *silently*, since the difference shows up as a
  systematic drift in answer length rather than as an error. It is
  available deliberately, as `length_normalized=True` -- see below for
  what measurement argued for having it at all.
- **The prompt is masked exactly as in supervised fine-tuning.** Only the
  response tokens count toward log pi(y|x); the prompt is conditioning.
- **log_softmax runs in fp32 even under fp16 autocast.** These are sums of
  a few hundred log-probabilities, each around -2 to -10, and fp16 has
  ~3 decimal digits near those magnitudes. The difference of two such sums
  is the entire training signal, so computing it in fp16 would leave the
  gradient made mostly of rounding.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from ashugpt.data.instruction import IGNORE_INDEX
from ashugpt.model.gpt import GPTOutput


def sequence_logprobs(logits: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
    """Summed log-probability of each sequence's supervised tokens.

    logits: (batch, seq_len, vocab_size) -- already shifted, as everywhere
    in this repo: logits[t] predicts labels[t].
    labels: (batch, seq_len) with IGNORE_INDEX where nothing is supervised.

    Returns (batch,).
    """
    mask = labels != IGNORE_INDEX
    # gather() cannot take -100 as an index, so the masked positions get a
    # harmless 0 and are removed afterwards by the mask.
    safe_labels = labels.masked_fill(~mask, 0)

    log_probs = F.log_softmax(logits.float(), dim=-1)
    token_logprobs = log_probs.gather(dim=-1, index=safe_labels.unsqueeze(-1)).squeeze(-1)
    return (token_logprobs * mask).sum(dim=-1)


def _ranking_accuracy(margins: torch.Tensor) -> torch.Tensor:
    """Fraction of positive margins, counting a tie as half."""
    return ((margins > 0).float() + 0.5 * (margins == 0).float()).mean()


@dataclass
class DPOMetrics:
    """Everything one batch of pairs says, kept together.

    The rewards are what the objective is made of. The four policy fields
    below are not used by the loss at all -- they are here because the
    rewards alone cannot answer the question anyone actually asks of a
    preference run, which is whether the model got better or merely learned
    which answer is longer. See `raw_accuracy` and `length_normalized_accuracy`.
    """

    loss: torch.Tensor
    chosen_reward: torch.Tensor
    rejected_reward: torch.Tensor
    policy_chosen_logps: torch.Tensor
    policy_rejected_logps: torch.Tensor
    chosen_tokens: torch.Tensor
    rejected_tokens: torch.Tensor

    @property
    def accuracy(self) -> torch.Tensor:
        """Fraction of pairs the implicit reward already ranks correctly.

        This is the number to watch, and the one that makes DPO easy to
        misread: it starts at exactly 50% and rises fast, but it says the
        model ranks *these two answers* correctly -- not that its answers
        got better.

        Ties count as half a correct ranking, which is not a rounding
        convenience. At step 0 the policy *is* the reference, so both
        implicit rewards are identically zero on every pair and every
        comparison is a tie; scoring `chosen > rejected` strictly would
        report 0% accuracy for a model that is, correctly, undecided, and
        the first evaluation of every run would look like a catastrophe.
        Half credit for a tie is what "undecided" is worth."""
        return _ranking_accuracy(self.chosen_reward - self.rejected_reward)

    @property
    def margin(self) -> torch.Tensor:
        return (self.chosen_reward - self.rejected_reward).mean()

    @property
    def raw_accuracy(self) -> torch.Tensor:
        """Does the policy prefer the chosen answer *at all*, reference aside?

        `accuracy` is measured against the frozen reference, so it is 50% by
        construction at step 0 and cannot be compared across models anchored
        to different references -- it says the policy moved in the right
        direction, not that it ends up in a good place. This one asks the
        model alone: is log pi(chosen) > log pi(rejected)? Any checkpoint can
        be scored with it, including the SFT model the run started from,
        which is what makes a before/after table mean something.
        """
        return _ranking_accuracy(self.policy_chosen_logps - self.policy_rejected_logps)

    @property
    def length_normalized_accuracy(self) -> torch.Tensor:
        """`raw_accuracy` with each answer's log-probability divided by its
        length -- the control for the one shortcut that would fake it.

        Summed log-probabilities are all negative, so a longer answer scores
        lower simply for being longer. A preference set whose chosen answers
        are shorter than its rejected ones can therefore be "learned" by a
        model that only ever learned to prefer brevity, and `raw_accuracy`
        would not notice. Per-token, that advantage disappears. A gap between
        the two numbers is the size of the length effect, not an error.
        """
        per_token = self.policy_chosen_logps / self.chosen_tokens - (
            self.policy_rejected_logps / self.rejected_tokens
        )
        return _ranking_accuracy(per_token)


def dpo_loss(
    policy_chosen_logps: torch.Tensor,
    policy_rejected_logps: torch.Tensor,
    reference_chosen_logps: torch.Tensor,
    reference_rejected_logps: torch.Tensor,
    beta: float = 0.1,
    chosen_tokens: torch.Tensor | None = None,
    rejected_tokens: torch.Tensor | None = None,
) -> DPOMetrics:
    """The DPO objective, plus the implicit rewards it is built from.

    The first four arguments are (batch,) summed sequence log-probabilities.
    The token counts are diagnostics only -- nothing in the loss uses them --
    and default to ones, which makes `length_normalized_accuracy` degenerate
    to `raw_accuracy` rather than divide by zero.
    """
    chosen_reward = beta * (policy_chosen_logps - reference_chosen_logps)
    rejected_reward = beta * (policy_rejected_logps - reference_rejected_logps)

    # -log sigmoid(x) computed as softplus(-x): equivalent, and finite for
    # large negative x, where log(sigmoid(x)) would underflow to -inf and
    # take the gradient with it. Early in training on a hard pair this is
    # exactly the regime the loss sits in.
    loss = F.softplus(-(chosen_reward - rejected_reward)).mean()
    return DPOMetrics(
        loss=loss,
        chosen_reward=chosen_reward,
        rejected_reward=rejected_reward,
        policy_chosen_logps=policy_chosen_logps,
        policy_rejected_logps=policy_rejected_logps,
        chosen_tokens=torch.ones_like(policy_chosen_logps) if chosen_tokens is None else chosen_tokens,
        rejected_tokens=torch.ones_like(policy_rejected_logps) if rejected_tokens is None else rejected_tokens,
    )


class DPOModel(nn.Module):
    """A policy and its frozen reference, presented as one model.

    This exists so DPO can reuse `ashugpt.training.trainer.train()` whole
    -- the LR schedule, gradient accumulation, mixed precision, logging,
    checkpointing and resume are all the same, and the only thing that
    differs is how a batch becomes a loss. Reimplementing a training loop
    to change one line of arithmetic is how two training loops end up
    disagreeing about something subtle six months later.

    The batch shape is what makes it fit: a preference example is
    (2, seq_len) -- chosen on row 0, rejected on row 1 -- so a batch is
    (batch, 2, seq_len) and still just "input_ids and labels" as far as the
    DataLoader, `split_batch` and the trainer are concerned. This module
    flattens the pair into a (2 * batch, seq_len) forward pass, which is
    also the efficient thing to do: one pass, not two.

    `state_dict()` deliberately returns the *policy's* state dict, not this
    wrapper's. What a DPO run produces is a model, not a model-plus-its-own
    history, and a checkpoint that carried both under prefixed keys could
    not be loaded by inference, evaluation, or a later fine-tune.

    That override is also the reason this cannot be trained under FSDP.
    FSDP checkpointing does not call `state_dict()` at all -- it gathers
    shards through torch.distributed's own `get_model_state_dict`, which
    walks the real module tree and would write exactly the prefixed
    two-model file the override exists to prevent. `scripts/preference_tune.py`
    refuses `parallel: fsdp` up front rather than discovering it at the
    first checkpoint. DDP is unaffected: it wraps this module whole, and
    the frozen reference's parameters are excluded from its reducer by
    requires_grad alone.
    """

    # Read by ashugpt.eval.perplexity.evaluate, which reports a perplexity
    # only for a cross-entropy loss. Every other model in this repo has one
    # and says nothing; this one has a ranking loss and has to say so, or
    # the validation log grows a column of exp(DPO loss).
    loss_is_cross_entropy = False

    def __init__(
        self,
        policy: nn.Module,
        reference: nn.Module,
        beta: float = 0.1,
        length_normalized: bool = False,
    ) -> None:
        super().__init__()
        self.policy = policy
        self.reference = reference
        self.beta = beta
        self.length_normalized = length_normalized

        # Frozen, and frozen in two senses: no gradients (which also keeps
        # it out of build_optimizer, which filters on requires_grad), and
        # permanently in eval mode.
        self.reference.eval()
        for parameter in self.reference.parameters():
            parameter.requires_grad_(False)

        self.last_metrics: DPOMetrics | None = None

    @property
    def config(self):
        return self.policy.config

    def train(self, mode: bool = True) -> DPOModel:
        """Puts the *policy* in train mode and leaves the reference in eval.

        Not a detail: this model has no dropout, but the reference must
        never be affected by anything the training loop does to the module
        it holds -- the moment the reference changes, the implicit reward
        is measured against a moving baseline and the objective no longer
        means what the derivation says it means."""
        super().train(mode)
        self.reference.eval()
        return self

    def set_memory_optimizations(self, **kwargs) -> None:
        """Forwarded to both models: the trainer calls this on whatever it
        is training, and the reference runs the same forward pass."""
        self.policy.set_memory_optimizations(**kwargs)
        self.reference.set_memory_optimizations(**kwargs)

    def num_parameters(self, exclude_embeddings: bool = False) -> int:
        return self.policy.num_parameters(exclude_embeddings=exclude_embeddings)

    def state_dict(self, *args, **kwargs):  # type: ignore[override]
        return self.policy.state_dict(*args, **kwargs)

    def load_state_dict(self, state_dict, *args, **kwargs):  # type: ignore[override]
        return self.policy.load_state_dict(state_dict, *args, **kwargs)

    def forward(self, input_ids: torch.Tensor, labels: torch.Tensor | None = None, **kwargs) -> GPTOutput:
        """input_ids/labels: (batch, 2, seq_len), chosen first.

        Returns a GPTOutput whose `.loss` is the DPO loss, so the trainer
        cannot tell the difference. The per-pair metrics are stashed on
        `last_metrics` for the caller to log -- they are diagnostics, not
        part of the objective.
        """
        # Packing is the case this refuses. A packed batch carries segment_ids
        # and position_ids that the trainer splats in here, and dropping them
        # would run block-diagonal-masked data under a plain causal mask --
        # silently, and with a loss curve that still falls.
        if kwargs:
            raise TypeError(f"DPO does not support {sorted(kwargs)}: preference pairs are never packed")
        if input_ids.dim() != 3 or input_ids.shape[1] != 2:
            raise ValueError(
                f"DPO expects (batch, 2, seq_len) with chosen first and rejected second, got {tuple(input_ids.shape)}"
            )
        if labels is None:
            raise ValueError("DPO has no unsupervised mode: labels are what mark the response tokens")

        batch = input_ids.shape[0]
        flat_ids = input_ids.reshape(batch * 2, -1)
        flat_labels = labels.reshape(batch * 2, -1)

        policy_logps = sequence_logprobs(self.policy(flat_ids).logits, flat_labels)
        with torch.no_grad():
            reference_logps = sequence_logprobs(self.reference(flat_ids).logits, flat_labels)

        # Rows alternate chosen/rejected per example after the reshape.
        policy_chosen, policy_rejected = policy_logps.view(batch, 2).unbind(dim=1)
        reference_chosen, reference_rejected = reference_logps.view(batch, 2).unbind(dim=1)

        supervised = (flat_labels != IGNORE_INDEX).sum(dim=-1).view(batch, 2)
        chosen_tokens, rejected_tokens = supervised.unbind(dim=1)

        if self.length_normalized:
            # Every term becomes a per-token average, so an answer is no
            # longer penalized for being long. Both the policy's and the
            # reference's log-probability for one answer are divided by the
            # same count -- they score the identical tokens -- so the
            # implicit reward stays a difference of comparable quantities.
            policy_chosen = policy_chosen / chosen_tokens
            policy_rejected = policy_rejected / rejected_tokens
            reference_chosen = reference_chosen / chosen_tokens
            reference_rejected = reference_rejected / rejected_tokens

        metrics = dpo_loss(
            policy_chosen,
            policy_rejected,
            reference_chosen,
            reference_rejected,
            beta=self.beta,
            chosen_tokens=chosen_tokens,
            rejected_tokens=rejected_tokens,
        )
        self.last_metrics = metrics
        return GPTOutput(logits=None, loss=metrics.loss)
