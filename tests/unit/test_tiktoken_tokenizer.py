"""Tests for the tiktoken-backed production tokenizer.

The from-scratch BPETokenizer is tested against the *algorithm* (that its
merges are learned and applied correctly). There is no point re-testing GPT-2's
published merge table that way -- it is a fixed published artifact, and tiktoken
is what implements it. What these tests cover instead is the wrapper's own
contract: the special-token layout it defines on top of GPT-2's vocabulary, and
that it is genuinely substitutable for BPETokenizer everywhere the rest of the
codebase touches a tokenizer.
"""

from __future__ import annotations

import pytest

from ashugpt.tokenizer import BPETokenizer, TiktokenBPETokenizer, load_tokenizer
from ashugpt.tokenizer.tiktoken_bpe import PAD_ID, UNK_ID, VOCAB_SIZE


@pytest.fixture(scope="module")
def tokenizer() -> TiktokenBPETokenizer:
    return TiktokenBPETokenizer()


def test_special_token_layout(tokenizer: TiktokenBPETokenizer) -> None:
    # bos deliberately aliases eos (GPT-2's <|endoftext|>): a document in a
    # pretraining stream really is preceded by the separator token, so priming
    # generation with it matches what training saw.
    assert tokenizer.bos_id == tokenizer.eos_id == 50256
    assert tokenizer.pad_id == PAD_ID == 50257
    assert tokenizer.unk_id == UNK_ID == 50258
    assert tokenizer.vocab_size == VOCAB_SIZE == 50259


def test_vocab_fits_uint16(tokenizer: TiktokenBPETokenizer) -> None:
    """scripts/prepare_data.py stores token ids as uint16 -- that is the whole
    reason a 5B-token corpus is 10GB on disk rather than 40GB. If the vocab
    ever outgrew uint16 that storage format would silently corrupt."""
    assert tokenizer.vocab_size <= 65536


def test_roundtrip_preserves_text(tokenizer: TiktokenBPETokenizer) -> None:
    text = "Once upon a time, there was a little robot named Ashu."
    assert tokenizer.decode(tokenizer.encode(text)) == text


def test_roundtrip_survives_unicode(tokenizer: TiktokenBPETokenizer) -> None:
    # Byte-level BPE should handle anything, including text no merge table was
    # trained on -- that is the property that makes unk_id unreachable.
    text = "emoji: 🤖 accents: café 中文 — dashes"
    assert tokenizer.decode(tokenizer.encode(text)) == text


def test_add_bos_and_eos(tokenizer: TiktokenBPETokenizer) -> None:
    ids = tokenizer.encode("hello", add_bos=True, add_eos=True)
    assert ids[0] == tokenizer.bos_id
    assert ids[-1] == tokenizer.eos_id
    # Specials are stripped by default, so the round trip is unchanged by them.
    assert tokenizer.decode(ids) == "hello"
    assert tokenizer.decode(ids, skip_special_tokens=False) == "<|endoftext|>hello<|endoftext|>"


def test_literal_endoftext_in_text_is_not_a_special_token(tokenizer: TiktokenBPETokenizer) -> None:
    """Corpus text must not be able to inject document boundaries. encode()
    uses encode_ordinary precisely so that a document containing the literal
    string "<|endoftext|>" tokenizes as characters, not as a separator."""
    ids = tokenizer.encode("<|endoftext|>")
    assert tokenizer.eos_id not in ids
    assert len(ids) > 1
    assert tokenizer.decode(ids) == "<|endoftext|>"


def test_decode_renders_out_of_table_specials(tokenizer: TiktokenBPETokenizer) -> None:
    """PAD/UNK live outside tiktoken's table entirely, so decoding them cannot
    go through it. They must still render rather than raise."""
    ids = tokenizer.encode("hi") + [tokenizer.pad_id, tokenizer.unk_id]
    assert tokenizer.decode(ids) == "hi"  # stripped by default
    assert tokenizer.decode(ids, skip_special_tokens=False) == "hi<|pad|><|unk|>"


def test_encode_batch_pads_and_masks(tokenizer: TiktokenBPETokenizer) -> None:
    batch = tokenizer.encode_batch(["hi", "a longer sentence here"], padding=True)
    lengths = {len(ids) for ids in batch["input_ids"]}
    assert len(lengths) == 1, "padding should make every row the same length"

    for ids, mask in zip(batch["input_ids"], batch["attention_mask"]):
        assert len(ids) == len(mask)
        # Every masked-out position must be padding, and only padding.
        for token, keep in zip(ids, mask):
            assert (token == tokenizer.pad_id) == (keep == 0)


def test_encode_batch_matches_encode(tokenizer: TiktokenBPETokenizer) -> None:
    texts = ["first document", "second, longer document"]
    batched = tokenizer.encode_batch(texts, add_eos=True, padding=False)["input_ids"]
    assert batched == [tokenizer.encode(t, add_eos=True) for t in texts]


def test_encode_batch_respects_max_length(tokenizer: TiktokenBPETokenizer) -> None:
    batch = tokenizer.encode_batch(["a much longer sentence than three tokens"], max_length=3, padding=False)
    assert len(batch["input_ids"][0]) == 3


def test_save_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "tok.json"
    TiktokenBPETokenizer().save(path)
    loaded = TiktokenBPETokenizer.load(path)
    assert loaded.vocab_size == VOCAB_SIZE
    assert loaded.encode("hello") == TiktokenBPETokenizer().encode("hello")


def test_load_tokenizer_dispatches_on_type(tmp_path) -> None:
    """A checkpoint is only decodable with the tokenizer it was trained with,
    so scripts resolve tokenizers through load_tokenizer() rather than by
    hardcoding a class. Both file formats must route correctly."""
    tiktoken_path = tmp_path / "tiktoken.json"
    TiktokenBPETokenizer().save(tiktoken_path)
    assert isinstance(load_tokenizer(tiktoken_path), TiktokenBPETokenizer)

    # From-scratch files predate the "type" field and are identified by its
    # absence -- tokenizer files saved before the wrapper existed still load.
    scratch_path = tmp_path / "scratch.json"
    BPETokenizer.train("hello world, hello again", vocab_size=270).save(scratch_path)
    assert isinstance(load_tokenizer(scratch_path), BPETokenizer)


def test_rejects_unknown_encoding(tmp_path) -> None:
    path = tmp_path / "tok.json"
    path.write_text('{"type": "tiktoken", "encoding": "cl100k_base"}', encoding="utf-8")
    with pytest.raises(ValueError, match="only supports"):
        TiktokenBPETokenizer.load(path)


def test_interface_parity_with_scratch_tokenizer(tokenizer: TiktokenBPETokenizer) -> None:
    """Everything downstream (dataset prep, training, generation, the API) is
    written against the interface, never against a concrete class."""
    scratch = BPETokenizer.train("hello world", vocab_size=270)
    for attribute in ("vocab_size", "pad_id", "bos_id", "eos_id", "unk_id"):
        assert hasattr(tokenizer, attribute), attribute
        assert hasattr(scratch, attribute), attribute
    for method in ("encode", "decode", "encode_batch", "save"):
        assert callable(getattr(tokenizer, method)), method
        assert callable(getattr(scratch, method)), method
