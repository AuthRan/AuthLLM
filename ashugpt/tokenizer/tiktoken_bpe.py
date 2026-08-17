"""GPT-2 byte-level BPE via `tiktoken`, behind the same interface as the
from-scratch `BPETokenizer`.

SPEC.md always planned this split ("Tokenizer: hybrid") and it is the one
piece of that plan that never got built: the from-scratch trainer/encoder in
`bpe_scratch.py` is the pedagogical artifact -- it is what proves the
algorithm is understood -- while real training runs need a tokenizer that can
chew through gigabytes without becoming the bottleneck.

The measured difference is why both exist. The from-scratch encoder runs at
roughly 9 MB/s of text in pure Python; more importantly, *training* merges is
O(vocab_size x corpus) with a full re-scan per merge, which is affordable for
the 1k-vocab fixture corpora in `tests/` and not affordable for a 50k-merge
vocab over a multi-GB corpus. tiktoken skips training entirely: GPT-2's merge
table is already published, and its encoder is a Rust extension.

Nothing about the *model* changes. This class only decides how text becomes
integers; every architectural claim in README.md is unaffected.

## Special tokens

GPT-2's published vocabulary has exactly one special token, `<|endoftext|>`
(id 50256), used as the document separator. This wrapper follows that
convention rather than inventing a parallel one:

    eos_id = bos_id = 50256    GPT-2's native <|endoftext|>
    pad_id = 50257             appended; never appears in pretraining data
    unk_id = 50258             appended; unreachable -- see below
    vocab_size = 50259

`bos_id` deliberately *aliases* `eos_id`. In a pretraining stream documents
are joined as `... doc <|endoftext|> doc <|endoftext|> ...`, so the token
that actually precedes the start of a document is `<|endoftext|>` itself.
Generating with `add_bos=True` therefore primes the model with a token it saw
at every document boundary during training, instead of a novel id it would
have to learn from nothing.

`unk_id` exists only for interface parity with `BPETokenizer`. A byte-level
BPE cannot produce an unknown token -- every byte sequence encodes by
construction -- so nothing in this class ever emits it. It costs one unused
embedding row.

Note that these ids differ from `BPETokenizer`'s (which numbers its four
special tokens 0-3). The two tokenizers are interchangeable through this
interface, *not* through their id spaces: a checkpoint trained with one
cannot be decoded with the other.
"""

from __future__ import annotations

import json
from pathlib import Path

ENCODING_NAME = "gpt2"

# GPT-2's published vocabulary: 50257 entries, the last of which is
# <|endoftext|>. The three ids below extend past it.
_GPT2_N_VOCAB = 50257
_ENDOFTEXT_ID = _GPT2_N_VOCAB - 1  # 50256

PAD_ID = _GPT2_N_VOCAB  # 50257
UNK_ID = _GPT2_N_VOCAB + 1  # 50258
VOCAB_SIZE = _GPT2_N_VOCAB + 2  # 50259


class TiktokenBPETokenizer:
    """Drop-in production tokenizer: same attribute/method surface as
    `BPETokenizer`, backed by tiktoken's GPT-2 encoding."""

    def __init__(self) -> None:
        try:
            import tiktoken
        except ImportError as e:  # pragma: no cover - exercised only without the optional dep
            raise ImportError(
                "TiktokenBPETokenizer needs the optional `tiktoken` dependency (pip install tiktoken). "
                "The from-scratch BPETokenizer has no extra dependencies if you'd rather use that."
            ) from e

        self._encoding = tiktoken.get_encoding(ENCODING_NAME)
        if self._encoding.n_vocab != _GPT2_N_VOCAB:
            raise ValueError(
                f"tiktoken's '{ENCODING_NAME}' encoding reports n_vocab={self._encoding.n_vocab}, "
                f"expected {_GPT2_N_VOCAB} -- the special-token ids in this module assume GPT-2's layout"
            )

        self.pad_id = PAD_ID
        self.unk_id = UNK_ID
        self.eos_id = _ENDOFTEXT_ID
        self.bos_id = _ENDOFTEXT_ID  # aliases eos -- see module docstring
        self._special_ids = {_ENDOFTEXT_ID, PAD_ID, UNK_ID}

    @property
    def vocab_size(self) -> int:
        return VOCAB_SIZE

    # ---- encoding / decoding ----

    def encode(self, text: str, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        # encode_ordinary (not encode) so that a literal "<|endoftext|>"
        # appearing in corpus text is tokenized as ordinary characters rather
        # than promoted into a real separator token -- untrusted corpus text
        # must not be able to inject document boundaries.
        ids = self._encoding.encode_ordinary(text)
        if add_bos:
            ids = [self.bos_id] + ids
        if add_eos:
            ids = ids + [self.eos_id]
        return ids

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        if skip_special_tokens:
            kept = [i for i in ids if i not in self._special_ids]
        else:
            # PAD/UNK are outside tiktoken's table entirely, so they can't be
            # round-tripped through it -- they're rendered by name, and only
            # the runs between them get handed to tiktoken.
            return "".join(self._decode_with_specials(ids))
        return self._encoding.decode(kept)

    def _decode_with_specials(self, ids: list[int]) -> list[str]:
        pieces: list[str] = []
        run: list[int] = []
        names = {_ENDOFTEXT_ID: "<|endoftext|>", PAD_ID: "<|pad|>", UNK_ID: "<|unk|>"}
        for id_ in ids:
            if id_ in names:
                if run:
                    pieces.append(self._encoding.decode(run))
                    run = []
                pieces.append(names[id_])
            else:
                run.append(id_)
        if run:
            pieces.append(self._encoding.decode(run))
        return pieces

    def encode_batch(
        self,
        texts: list[str],
        add_bos: bool = False,
        add_eos: bool = False,
        max_length: int | None = None,
        padding: bool = True,
    ) -> dict[str, list[list[int]]]:
        """Same contract as `BPETokenizer.encode_batch`: right-padded
        `input_ids` plus a matching `attention_mask`."""
        # encode_ordinary_batch releases the GIL per document, so this is
        # genuinely parallel across cores -- the reason bulk tokenization in
        # scripts/prepare_data.py goes through here rather than encode().
        encoded = self._encoding.encode_ordinary_batch(texts)
        if add_bos or add_eos:
            prefix = [self.bos_id] if add_bos else []
            suffix = [self.eos_id] if add_eos else []
            encoded = [prefix + ids + suffix for ids in encoded]
        if max_length is not None:
            encoded = [ids[:max_length] for ids in encoded]

        if not padding:
            return {"input_ids": encoded, "attention_mask": [[1] * len(ids) for ids in encoded]}

        target_len = max((len(ids) for ids in encoded), default=0)
        input_ids, attention_mask = [], []
        for ids in encoded:
            pad_len = target_len - len(ids)
            input_ids.append(ids + [self.pad_id] * pad_len)
            attention_mask.append([1] * len(ids) + [0] * pad_len)
        return {"input_ids": input_ids, "attention_mask": attention_mask}

    # ---- persistence ----

    def save(self, path: str | Path) -> None:
        """There are no learned parameters to store -- GPT-2's merge table
        ships with tiktoken. The file is a marker recording *which* tokenizer
        a checkpoint was trained with, so `load_tokenizer()` can dispatch."""
        data = {"type": "tiktoken", "encoding": ENCODING_NAME, "vocab_size": VOCAB_SIZE}
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "TiktokenBPETokenizer":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("encoding") != ENCODING_NAME:
            raise ValueError(
                f"Tokenizer file requests encoding {data.get('encoding')!r}, but this class only supports "
                f"{ENCODING_NAME!r}"
            )
        return cls()
