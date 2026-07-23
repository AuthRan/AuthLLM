"""Autoregressive text generation, with an optional KV cache.

CausalSelfAttention/TransformerBlock/AshuGPT have accepted and threaded
through kv_cache/position_offset since Milestone 3-4, specifically so this
moment wouldn't require touching them -- this module is what actually
*uses* that capability.

Without a cache (`use_cache=False`), every step re-runs the model over the
*entire* sequence generated so far: simple, but O(n) redundant work per new
token (attention recomputes every earlier position's keys/values again,
even though only the newest position's output is used).

With a cache (the default), the shape of the work changes:

    First forward pass (the whole prompt, length P):
        model(prompt) -> K/V computed and cached for all P prompt
        positions, at every layer. logits[:, -1, :] gives the first
        next-token prediction.

    Every step after that (just the ONE new token):
        model(new_token, kv_caches=<cache so far>, position_offset=<cache length>)
        -> the new token's Q attends over the cached K/V (all earlier
        positions) plus its own freshly computed K/V; the cache grows by
        one position at every layer; only the new token was actually run
        through the network.

Both paths sample the same way and produce the same sequence of tokens
(see tests/unit/test_generate.py's cache-equivalence tests) -- caching
changes how much redundant computation happens, not what gets computed.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from ashugpt.tokenizer import BPETokenizer


def _filter_top_k(logits: torch.Tensor, top_k: int) -> torch.Tensor:
    """Keep only the top_k highest logits per row; mask the rest to -inf."""
    if top_k <= 0:
        raise ValueError(f"top_k must be positive, got {top_k}")
    top_k = min(top_k, logits.size(-1))  # can't keep more candidates than exist
    threshold = torch.topk(logits, top_k, dim=-1).values[:, -1, None]  # k-th largest value, per row
    return logits.masked_fill(logits < threshold, float("-inf"))


def _filter_top_p(logits: torch.Tensor, top_p: float) -> torch.Tensor:
    """Nucleus sampling: keep the smallest set of highest-probability
    tokens whose cumulative probability is >= top_p; mask the rest."""
    if not 0.0 < top_p <= 1.0:
        raise ValueError(f"top_p must be in (0, 1], got {top_p}")

    sorted_logits, sorted_indices = torch.sort(logits, dim=-1, descending=True)
    cumulative_probs = torch.cumsum(F.softmax(sorted_logits, dim=-1), dim=-1)

    # Drop a (sorted-order) token once the cumulative probability *up to
    # and including the token before it* already reached top_p -- i.e.
    # keep the token that crosses the threshold, drop everything after it.
    sorted_to_remove = cumulative_probs > top_p
    sorted_to_remove[..., 1:] = sorted_to_remove[..., :-1].clone()
    sorted_to_remove[..., 0] = False  # always keep at least the single most likely token

    # sorted_indices is a full permutation of the vocab per row, so this
    # scatter maps every "remove" flag from sorted order back to its
    # original vocab position.
    to_remove = sorted_to_remove.scatter(-1, sorted_indices, sorted_to_remove)
    return logits.masked_fill(to_remove, float("-inf"))


def sample_next_token(
    logits: torch.Tensor,
    temperature: float = 1.0,
    top_k: int | None = None,
    top_p: float | None = None,
) -> torch.Tensor:
    """logits: (batch, vocab_size) -- the final position's logits only.
    Returns (batch,) next-token ids.

    temperature=0.0 means greedy decoding (argmax) -- dividing by zero
    temperature is undefined, so it's special-cased rather than run
    through the sampling math below.
    """
    if temperature < 0.0:
        raise ValueError(f"temperature must be >= 0, got {temperature}")
    if temperature == 0.0:
        return logits.argmax(dim=-1)

    logits = logits / temperature
    if top_k is not None:
        logits = _filter_top_k(logits, top_k)
    if top_p is not None:
        logits = _filter_top_p(logits, top_p)

    # Every row is guaranteed to have at least one finite (non -inf) logit
    # -- top-k always keeps >=1, top-p always keeps the top token -- so
    # softmax here can never collapse a whole row to zero/NaN.
    probs = F.softmax(logits, dim=-1)
    return torch.multinomial(probs, num_samples=1).squeeze(-1)


@torch.no_grad()
def generate(
    model: nn.Module,
    input_ids: torch.Tensor,
    max_new_tokens: int,
    temperature: float = 1.0,
    top_k: int | None = None,
    top_p: float | None = None,
    eos_id: int | None = None,
    use_cache: bool = True,
) -> torch.Tensor:
    """input_ids: (batch, prompt_len). Returns (batch, prompt_len + n),
    n <= max_new_tokens (shorter if every row in the batch hit eos_id first).

    A row that has already emitted eos_id is padded with more eos_id
    rather than left to keep sampling, so the batch tensor stays
    rectangular without corrupting already-finished rows; decode() already
    strips all special tokens regardless of position, so callers don't
    need to trim anything themselves.

    use_cache=True (default) reuses cached K/V across steps -- see the
    module docstring for the shapes at each stage. use_cache=False
    recomputes the whole sequence every step instead; both must produce
    the same generated tokens (that equivalence is what
    tests/unit/test_generate.py's cache tests check), so False exists
    mainly as a correctness baseline, not something to prefer.
    """
    was_training = model.training
    model.eval()

    batch_size, prompt_len = input_ids.shape
    context_length = model.config.context_length
    if prompt_len + max_new_tokens > context_length:
        raise ValueError(
            f"prompt_len ({prompt_len}) + max_new_tokens ({max_new_tokens}) exceeds "
            f"this model's context_length ({context_length}). Neither caching nor "
            f"the uncached path implements sliding-window truncation, so the full "
            f"generated sequence must always fit in one context window."
        )

    generated = input_ids
    finished = torch.zeros(batch_size, dtype=torch.bool, device=input_ids.device)

    next_input = input_ids  # first call always processes the whole prompt
    position_offset = 0
    kv_caches = None

    for _ in range(max_new_tokens):
        output = model(next_input, kv_caches=kv_caches, position_offset=position_offset)
        next_token_logits = output.logits[:, -1, :]  # (batch, vocab_size) -- the newest position only
        next_tokens = sample_next_token(next_token_logits, temperature, top_k, top_p)  # (batch,)

        if eos_id is not None:
            next_tokens = torch.where(finished, torch.full_like(next_tokens, eos_id), next_tokens)

        generated = torch.cat([generated, next_tokens.unsqueeze(1)], dim=1)

        if eos_id is not None:
            finished = finished | (next_tokens == eos_id)
            if finished.all():
                break

        if use_cache:
            kv_caches = output.kv_caches  # covers everything processed so far (not yet the just-sampled token)
            position_offset = generated.shape[1] - 1  # absolute position of the token we just appended
            next_input = next_tokens.unsqueeze(1)  # (batch, 1) -- only the new token needs to run next
        else:
            next_input = generated  # (batch, cur_len) -- recompute the whole sequence again

    if was_training:
        model.train()
    return generated


def generate_text(
    model: nn.Module,
    tokenizer: BPETokenizer,
    prompt: str,
    max_new_tokens: int,
    temperature: float = 1.0,
    top_k: int | None = None,
    top_p: float | None = None,
    add_bos: bool = True,
    use_cache: bool = True,
) -> str:
    """Single-prompt convenience wrapper: text in, text out."""
    input_ids = torch.tensor([tokenizer.encode(prompt, add_bos=add_bos)])
    output_ids = generate(
        model,
        input_ids,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        eos_id=tokenizer.eos_id,
        use_cache=use_cache,
    )
    return tokenizer.decode(output_ids[0].tolist())
