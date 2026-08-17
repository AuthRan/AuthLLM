"""Unit tests for CausalSelfAttention."""

import pytest
import torch

from ashugpt.model.attention import CausalSelfAttention, causal_mask


def make_attention(d_model: int = 32, n_heads: int = 4, max_seq_len: int = 16) -> CausalSelfAttention:
    torch.manual_seed(0)
    return CausalSelfAttention(d_model=d_model, n_heads=n_heads, n_kv_heads=n_heads, max_seq_len=max_seq_len)


def test_output_and_cache_shapes() -> None:
    attn = make_attention(d_model=32, n_heads=4)
    x = torch.randn(2, 10, 32)
    out, (k, v) = attn(x)
    assert out.shape == (2, 10, 32)
    assert k.shape == (2, 4, 10, 8)  # head_dim = 32 / 4 = 8
    assert v.shape == (2, 4, 10, 8)


def test_rejects_d_model_not_divisible_by_n_heads() -> None:
    with pytest.raises(ValueError):
        CausalSelfAttention(d_model=10, n_heads=3, n_kv_heads=3, max_seq_len=16)


def test_rejects_n_heads_not_divisible_by_n_kv_heads() -> None:
    with pytest.raises(ValueError, match="must be divisible by n_kv_heads"):
        CausalSelfAttention(d_model=32, n_heads=4, n_kv_heads=3, max_seq_len=16)


# ---- grouped-query attention (n_kv_heads < n_heads) ----


def make_gqa(d_model: int = 32, n_heads: int = 4, n_kv_heads: int = 2, max_seq_len: int = 16):
    torch.manual_seed(0)
    return CausalSelfAttention(d_model=d_model, n_heads=n_heads, n_kv_heads=n_kv_heads, max_seq_len=max_seq_len)


def test_gqa_shrinks_kv_projections_and_cache() -> None:
    """The saving GQA exists for: K/V projections and, crucially, the cache
    scale with n_kv_heads rather than n_heads."""
    attn = make_gqa(d_model=32, n_heads=4, n_kv_heads=2)
    assert attn.k_proj.weight.shape == (16, 32)  # 2 kv heads x head_dim 8
    assert attn.v_proj.weight.shape == (16, 32)
    assert attn.q_proj.weight.shape == (32, 32)  # queries keep every head

    x = torch.randn(2, 10, 32)
    out, (k, v) = attn(x)
    assert out.shape == (2, 10, 32), "output shape must not change"
    # The cache holds UNexpanded K/V -- half the heads, so half the memory.
    assert k.shape == (2, 2, 10, 8)
    assert v.shape == (2, 2, 10, 8)


def test_gqa_with_equal_heads_is_the_original_module() -> None:
    """n_kv_heads == n_heads must remain exactly plain multi-head attention --
    every existing config and checkpoint depends on it."""
    attn = make_gqa(d_model=32, n_heads=4, n_kv_heads=4)
    assert attn.n_groups == 1
    x = torch.randn(2, 10, 32)
    out, (k, v) = attn(x)
    assert out.shape == (2, 10, 32)
    assert k.shape == (2, 4, 10, 8)


def test_gqa_query_heads_map_to_the_right_kv_head() -> None:
    """The grouping must be interleaved, not blocked: query head i attends to
    K/V head i // n_groups. A plain repeat() instead of repeat_interleave()
    would silently pair the wrong heads and still produce correct *shapes*, so
    this checks the mapping directly against a manual reference.
    """
    torch.manual_seed(0)
    n_heads, n_kv_heads, head_dim, d_model = 4, 2, 8, 32
    attn = make_gqa(d_model=d_model, n_heads=n_heads, n_kv_heads=n_kv_heads)
    x = torch.randn(1, 6, d_model)

    out, (k_cache, v_cache) = attn(x)

    # Rebuild attention by hand, expanding K/V per query head explicitly.
    q = attn.q_proj(x).view(1, 6, n_heads, head_dim).transpose(1, 2)
    from ashugpt.model.rope import apply_rotary_pos_emb

    cos, sin = attn.rope(seq_len=6, offset=0)
    q = apply_rotary_pos_emb(q, cos, sin)

    mask = causal_mask(seq_len_q=6, seq_len_k=6, offset=0, device=x.device)
    heads = []
    for head in range(n_heads):
        kv_head = head // (n_heads // n_kv_heads)  # the mapping under test
        k_h = k_cache[:, kv_head]
        v_h = v_cache[:, kv_head]
        scores = (q[:, head] @ k_h.transpose(-2, -1)) / (head_dim**0.5)
        scores = scores.masked_fill(mask, float("-inf"))
        heads.append(torch.softmax(scores, dim=-1) @ v_h)

    manual = torch.stack(heads, dim=1).transpose(1, 2).reshape(1, 6, d_model)
    assert torch.allclose(out, attn.o_proj(manual), atol=1e-5)


def test_gqa_efficient_attention_matches_manual_path() -> None:
    """The fused SDPA path must agree with the manual path under GQA too --
    the expansion happens before either, but only one of them is the
    pedagogical reference."""
    attn = make_gqa(d_model=32, n_heads=8, n_kv_heads=2)
    x = torch.randn(2, 12, 32)

    attn.use_efficient_attention = False
    manual, _ = attn(x)
    attn.use_efficient_attention = True
    fused, _ = attn(x)

    assert torch.allclose(manual, fused, atol=1e-5)


def test_gqa_cached_generation_matches_uncached() -> None:
    """Feeding one token at a time through the cache must equal running the
    whole sequence at once -- the property KV caching is only useful if it has,
    now with fewer K/V heads in the cache."""
    attn = make_gqa(d_model=32, n_heads=4, n_kv_heads=2, max_seq_len=16)
    x = torch.randn(1, 6, 32)

    full, _ = attn(x)

    cache = None
    outputs = []
    for position in range(6):
        step, cache = attn(x[:, position : position + 1], kv_cache=cache, position_offset=position)
        outputs.append(step)
    incremental = torch.cat(outputs, dim=1)

    assert torch.allclose(full, incremental, atol=1e-5)


# ---- causal masking ----


def test_causal_mask_matrix_shape_no_offset() -> None:
    mask = causal_mask(seq_len_q=4, seq_len_k=4, offset=0, device=torch.device("cpu"))
    expected = torch.tensor(
        [
            [False, True, True, True],
            [False, False, True, True],
            [False, False, False, True],
            [False, False, False, False],
        ]
    )
    assert torch.equal(mask, expected)


def test_causal_mask_with_offset_allows_full_cache_attendance() -> None:
    # One new query token (seq_len_q=1) at absolute position 3 (offset=3),
    # attending over 4 cached+new keys (seq_len_k=4): nothing should be
    # blocked, since positions 0..3 are all <= the query's own position 3.
    mask = causal_mask(seq_len_q=1, seq_len_k=4, offset=3, device=torch.device("cpu"))
    assert not mask.any()


def test_model_cannot_attend_to_future_tokens() -> None:
    # The gold-standard causal test: changing a *later* token's content must
    # not change any *earlier* token's output, because that would mean the
    # earlier position "saw into the future".
    attn = make_attention(d_model=32, n_heads=4, max_seq_len=16)
    attn.eval()

    torch.manual_seed(1)
    x = torch.randn(2, 10, 32)
    x_modified = x.clone()
    x_modified[:, 5:, :] = torch.randn_like(x_modified[:, 5:, :])  # change positions 5..9

    with torch.no_grad():
        out_original, _ = attn(x)
        out_modified, _ = attn(x_modified)

    # Positions before the change: untouched.
    assert torch.allclose(out_original[:, :5, :], out_modified[:, :5, :], atol=1e-6)
    # Positions at/after the change: the input there actually changed, so
    # the test setup is meaningful (not vacuously true).
    assert not torch.allclose(out_original[:, 5:, :], out_modified[:, 5:, :], atol=1e-6)


# ---- KV-cache compatibility ----


def test_incremental_forward_matches_full_forward() -> None:
    # Processing a sequence in two chunks via kv_cache/position_offset must
    # give identical results to processing it all at once -- this is the
    # correctness property a future KV-cache manager (SPEC.md M10) depends on.
    attn = make_attention(d_model=32, n_heads=4, max_seq_len=16)
    attn.eval()

    torch.manual_seed(2)
    x = torch.randn(1, 8, 32)

    with torch.no_grad():
        out_full, _ = attn(x, kv_cache=None, position_offset=0)

        out_first, kv_first = attn(x[:, :5, :], kv_cache=None, position_offset=0)
        out_second, kv_second = attn(x[:, 5:, :], kv_cache=kv_first, position_offset=5)

    assert torch.allclose(out_full[:, :5, :], out_first, atol=1e-5)
    assert torch.allclose(out_full[:, 5:, :], out_second, atol=1e-5)

    k_full, v_full = attn(x, kv_cache=None, position_offset=0)[1]
    k_second, v_second = kv_second
    assert torch.allclose(k_full, k_second, atol=1e-5)
    assert torch.allclose(v_full, v_second, atol=1e-5)


def test_incremental_one_token_at_a_time_matches_full_forward() -> None:
    # The realistic autoregressive-generation case: one new token per call.
    attn = make_attention(d_model=16, n_heads=2, max_seq_len=16)
    attn.eval()

    torch.manual_seed(3)
    x = torch.randn(1, 6, 16)

    with torch.no_grad():
        out_full, _ = attn(x, kv_cache=None, position_offset=0)

        kv_cache = None
        outputs = []
        for t in range(x.shape[1]):
            out_t, kv_cache = attn(x[:, t : t + 1, :], kv_cache=kv_cache, position_offset=t)
            outputs.append(out_t)
        out_incremental = torch.cat(outputs, dim=1)

    assert torch.allclose(out_full, out_incremental, atol=1e-5)


# ---- efficient attention path (Milestone 9) ----


def test_efficient_attention_matches_manual_attention() -> None:
    attn = make_attention(d_model=32, n_heads=4)
    attn.eval()
    torch.manual_seed(2)
    x = torch.randn(2, 10, 32)

    with torch.no_grad():
        out_manual, _ = attn(x)
        attn.use_efficient_attention = True
        out_efficient, _ = attn(x)

    assert torch.allclose(out_manual, out_efficient, atol=1e-5)


def test_efficient_attention_still_cannot_attend_to_future_tokens() -> None:
    # The same causal-correctness property tested for the manual path in
    # test_model_cannot_attend_to_future_tokens, re-verified for the
    # separate SDPA code path -- it reuses the same mask, but it's a
    # different call site and deserves its own direct check.
    attn = make_attention(d_model=32, n_heads=4)
    attn.use_efficient_attention = True
    attn.eval()

    torch.manual_seed(1)
    x = torch.randn(2, 10, 32)
    x_modified = x.clone()
    x_modified[:, 5:, :] = torch.randn_like(x_modified[:, 5:, :])

    with torch.no_grad():
        out_original, _ = attn(x)
        out_modified, _ = attn(x_modified)

    assert torch.allclose(out_original[:, :5, :], out_modified[:, :5, :], atol=1e-6)
    assert not torch.allclose(out_original[:, 5:, :], out_modified[:, 5:, :], atol=1e-6)


def test_efficient_attention_incremental_forward_matches_full_forward() -> None:
    attn = make_attention(d_model=32, n_heads=4, max_seq_len=16)
    attn.use_efficient_attention = True
    attn.eval()

    torch.manual_seed(2)
    x = torch.randn(1, 8, 32)

    with torch.no_grad():
        out_full, _ = attn(x, kv_cache=None, position_offset=0)
        out_first, kv_first = attn(x[:, :5, :], kv_cache=None, position_offset=0)
        out_second, _ = attn(x[:, 5:, :], kv_cache=kv_first, position_offset=5)

    assert torch.allclose(out_full[:, :5, :], out_first, atol=1e-5)
    assert torch.allclose(out_full[:, 5:, :], out_second, atol=1e-5)
