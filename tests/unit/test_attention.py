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


def test_gqa_not_implemented_yet() -> None:
    with pytest.raises(NotImplementedError):
        CausalSelfAttention(d_model=32, n_heads=4, n_kv_heads=2, max_seq_len=16)


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
