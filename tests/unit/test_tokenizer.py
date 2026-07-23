"""Unit tests for the from-scratch BPE tokenizer."""

from pathlib import Path

import pytest

from ashugpt.tokenizer.bpe_scratch import (
    BASE_VOCAB_SIZE,
    _PRETOKEN_PATTERN,
    BPETokenizer,
)

CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_corpus.txt"
CORPUS_TEXT = CORPUS_PATH.read_text(encoding="utf-8")
TEST_VOCAB_SIZE = 300  # only 40 merges above the byte-level base -- fast to train


@pytest.fixture(scope="module")
def tokenizer() -> BPETokenizer:
    return BPETokenizer.train(CORPUS_TEXT, vocab_size=TEST_VOCAB_SIZE)


# ---- encode -> decode ----


@pytest.mark.parametrize(
    "text",
    [
        "Mia and Rex ran through the garden.",
        "",
        "   leading and trailing spaces   ",
        "multiple   spaces\tand\ttabs\n\nand newlines",
        "numbers 123 456 and punctuation!?.,;:'\"",
        "unicode: héllo wörld 你好 🚀🎉",  # never seen during training
    ],
)
def test_encode_decode_roundtrip(tokenizer: BPETokenizer, text: str) -> None:
    assert tokenizer.decode(tokenizer.encode(text)) == text


def test_pretokenizer_never_drops_characters() -> None:
    # Guards the invariant encode() relies on: every character in the input
    # ends up in some chunk, so no text is ever silently lost.
    tricky = "a  b\tc\n\nd  " + chr(39) + "s don" + chr(39) + "t 123abc 你好😀"
    assert "".join(_PRETOKEN_PATTERN.findall(tricky)) == tricky


# ---- special tokens ----


def test_special_token_ids_are_fixed_and_distinct(tokenizer: BPETokenizer) -> None:
    ids = {tokenizer.pad_id, tokenizer.bos_id, tokenizer.eos_id, tokenizer.unk_id}
    assert ids == {0, 1, 2, 3}


def test_add_bos_eos(tokenizer: BPETokenizer) -> None:
    ids = tokenizer.encode("hello", add_bos=True, add_eos=True)
    assert ids[0] == tokenizer.bos_id
    assert ids[-1] == tokenizer.eos_id
    assert tokenizer.decode(ids) == "hello"  # skip_special_tokens=True by default


def test_decode_can_show_special_tokens(tokenizer: BPETokenizer) -> None:
    ids = tokenizer.encode("hi", add_bos=True, add_eos=True)
    shown = tokenizer.decode(ids, skip_special_tokens=False)
    assert shown.startswith("<bos>")
    assert shown.endswith("<eos>")


def test_byte_level_bpe_never_needs_unk(tokenizer: BPETokenizer) -> None:
    # Because encoding operates on raw UTF-8 bytes, there is no such thing as
    # an out-of-vocabulary character -- unk_id should never appear in output
    # produced by encode(), even for text far outside the training corpus.
    weird_text = "완전히 다른 언어와 이모지 🐍🔥 and 123 numbers"
    ids = tokenizer.encode(weird_text)
    assert tokenizer.unk_id not in ids
    assert tokenizer.decode(ids) == weird_text


# ---- batch encoding ----


def test_batch_matches_individual_encode(tokenizer: BPETokenizer) -> None:
    texts = ["Mia and Rex.", "Pip found a carrot.", "Sam flew a kite."]
    batch = tokenizer.encode_batch(texts, padding=False)
    for text, ids in zip(texts, batch["input_ids"]):
        assert ids == tokenizer.encode(text)
    assert all(sum(mask) == len(ids) for mask, ids in zip(batch["attention_mask"], batch["input_ids"]))


# ---- padding ----


def test_padding_produces_equal_length_rows(tokenizer: BPETokenizer) -> None:
    texts = ["short", "a much longer sentence than the other one"]
    batch = tokenizer.encode_batch(texts, padding=True)
    lengths = {len(ids) for ids in batch["input_ids"]}
    assert len(lengths) == 1  # every row padded to the same length

    short_ids, short_mask = batch["input_ids"][0], batch["attention_mask"][0]
    num_real = sum(short_mask)
    assert short_ids[:num_real] == tokenizer.encode("short")
    assert short_ids[num_real:] == [tokenizer.pad_id] * (len(short_ids) - num_real)
    assert short_mask[num_real:] == [0] * (len(short_mask) - num_real)


def test_no_padding_keeps_variable_lengths(tokenizer: BPETokenizer) -> None:
    texts = ["short", "a much longer sentence than the other one"]
    batch = tokenizer.encode_batch(texts, padding=False)
    assert len(batch["input_ids"][0]) != len(batch["input_ids"][1])


# ---- truncation ----


def test_truncation_caps_length(tokenizer: BPETokenizer) -> None:
    long_text = CORPUS_TEXT[:500]
    full_ids = tokenizer.encode(long_text)
    assert len(full_ids) > 20  # sanity: this text really is longer than max_length

    batch = tokenizer.encode_batch([long_text], max_length=10, padding=False)
    assert batch["input_ids"][0] == full_ids[:10]
    assert len(batch["input_ids"][0]) == 10


def test_truncation_and_padding_together(tokenizer: BPETokenizer) -> None:
    texts = ["short", CORPUS_TEXT[:500]]
    batch = tokenizer.encode_batch(texts, max_length=10, padding=True)
    assert all(len(ids) == 10 for ids in batch["input_ids"])


# ---- training / vocab ----


def test_train_rejects_too_small_vocab_size() -> None:
    with pytest.raises(ValueError):
        BPETokenizer.train("some text", vocab_size=BASE_VOCAB_SIZE - 1)


def test_vocab_size_reaches_target(tokenizer: BPETokenizer) -> None:
    assert tokenizer.vocab_size == TEST_VOCAB_SIZE


def test_learns_a_frequent_word_as_one_token(tokenizer: BPETokenizer) -> None:
    # "the" is one of the most frequent words in the corpus, so BPE should
    # merge it into a single token well before rarer words get merged at all.
    the_ids = tokenizer.encode(" the")
    assert len(the_ids) == 1
    assert tokenizer.decode(the_ids) == " the"


# ---- save / load ----


def test_save_load_roundtrip(tokenizer: BPETokenizer, tmp_path: Path) -> None:
    path = tmp_path / "tokenizer.json"
    tokenizer.save(path)
    loaded = BPETokenizer.load(path)

    assert loaded.vocab_size == tokenizer.vocab_size
    assert loaded.merges == tokenizer.merges

    text = "Mia and Rex explored the forest together."
    assert loaded.encode(text) == tokenizer.encode(text)
    assert loaded.decode(loaded.encode(text)) == text
