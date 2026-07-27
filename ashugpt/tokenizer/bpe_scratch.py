"""A byte-level Byte-Pair Encoding (BPE) tokenizer, implemented from scratch.

How it works, in order:

1. Text is pre-tokenized into "chunks" (roughly: words, punctuation runs, and
   whitespace runs) with a regex, so that later merges never glue two
   different words together.
2. Each chunk is turned into raw UTF-8 bytes, and each byte (0-255) starts
   out as its own token. This means literally any Unicode text can be
   represented from the start -- there is no out-of-vocabulary character,
   which is why `<unk>` is reserved but never actually produced by encode().
3. Training repeatedly finds the most frequent *adjacent pair* of tokens
   across the whole corpus and merges it into one new token, until the
   target vocabulary size is reached. Frequent byte pairs become frequent
   sub-words ("th", "the", "ing", ...); rare sequences stay as raw bytes.
4. Encoding replays the learned merges (in the order they were learned) on
   new text. Decoding just concatenates each token's bytes back together
   and decodes as UTF-8 -- lossless as long as nothing was truncated.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

PAD_TOKEN = "<pad>"
BOS_TOKEN = "<bos>"
EOS_TOKEN = "<eos>"
UNK_TOKEN = "<unk>"

# Order fixes the IDs: pad=0, bos=1, eos=2, unk=3. Byte tokens start right after.
SPECIAL_TOKENS = [PAD_TOKEN, BOS_TOKEN, EOS_TOKEN, UNK_TOKEN]
NUM_SPECIAL_TOKENS = len(SPECIAL_TOKENS)
NUM_BYTE_TOKENS = 256
BYTE_OFFSET = NUM_SPECIAL_TOKENS
BASE_VOCAB_SIZE = NUM_SPECIAL_TOKENS + NUM_BYTE_TOKENS  # smallest valid vocab_size (no merges)

# Approximates GPT-2's pre-tokenizer regex using stdlib `re` (no \p{L} Unicode
# property support, so non-ASCII letters fall into the "other characters"
# bucket rather than getting their own letters-only chunk). Fine for an
# English-language corpus like TinyStories; a real Unicode-aware split would
# need the third-party `regex` package.
_PRETOKEN_PATTERN = re.compile(
    r"'s|'t|'re|'ve|'m|'ll|'d| ?[A-Za-z]+| ?[0-9]+| ?[^\sA-Za-z0-9]+|\s+"
)


def _merge_pair(ids: list[int], pair: tuple[int, int], new_id: int) -> list[int]:
    """Replace every non-overlapping occurrence of `pair` in `ids` with `new_id`."""
    merged: list[int] = []
    i = 0
    while i < len(ids):
        if i < len(ids) - 1 and (ids[i], ids[i + 1]) == pair:
            merged.append(new_id)
            i += 2
        else:
            merged.append(ids[i])
            i += 1
    return merged


class BPETokenizer:
    """Byte-level BPE tokenizer with pad/bos/eos/unk special tokens."""

    def __init__(self, merges: dict[tuple[int, int], int], vocab: dict[int, bytes]) -> None:
        self.merges = merges  # (id1, id2) -> merged_id, in the order they were learned
        self.vocab = vocab  # id -> raw bytes, for every non-special id
        self.pad_id, self.bos_id, self.eos_id, self.unk_id = range(NUM_SPECIAL_TOKENS)
        self._special_ids = set(range(NUM_SPECIAL_TOKENS))
        # Natural text repeats the same chunks ("the", " and", ...) constantly,
        # and a chunk's merge result depends only on the chunk. Memoizing per
        # unique chunk turns encoding a large corpus from re-encoding every
        # occurrence into one encode per distinct chunk -- hours -> seconds.
        self._chunk_cache: dict[str, list[int]] = {}

    @property
    def vocab_size(self) -> int:
        return NUM_SPECIAL_TOKENS + len(self.vocab)

    # ---- training ----

    @classmethod
    def train(cls, text: str, vocab_size: int) -> "BPETokenizer":
        if vocab_size < BASE_VOCAB_SIZE:
            raise ValueError(
                f"vocab_size must be >= {BASE_VOCAB_SIZE} "
                f"({NUM_SPECIAL_TOKENS} special tokens + {NUM_BYTE_TOKENS} byte tokens)"
            )

        vocab: dict[int, bytes] = {BYTE_OFFSET + b: bytes([b]) for b in range(NUM_BYTE_TOKENS)}

        # Count each unique chunk once and weight by frequency, instead of
        # rescanning the raw corpus on every merge -- much faster, same result.
        chunk_counts = Counter(_PRETOKEN_PATTERN.findall(text))
        sequences: dict[str, list[int]] = {
            chunk: [BYTE_OFFSET + b for b in chunk.encode("utf-8")] for chunk in chunk_counts
        }

        merges: dict[tuple[int, int], int] = {}
        next_id = BYTE_OFFSET + NUM_BYTE_TOKENS
        num_merges = vocab_size - next_id

        for _ in range(num_merges):
            pair_counts: Counter[tuple[int, int]] = Counter()
            for chunk, seq in sequences.items():
                freq = chunk_counts[chunk]
                for pair in zip(seq, seq[1:]):
                    pair_counts[pair] += freq

            if not pair_counts:
                break  # every chunk is now a single token -- nothing left to merge

            best_pair = max(pair_counts, key=pair_counts.get)
            vocab[next_id] = vocab[best_pair[0]] + vocab[best_pair[1]]
            merges[best_pair] = next_id
            sequences = {chunk: _merge_pair(seq, best_pair, next_id) for chunk, seq in sequences.items()}
            next_id += 1

        return cls(merges=merges, vocab=vocab)

    # ---- encode / decode ----

    def encode(self, text: str, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        ids: list[int] = [self.bos_id] if add_bos else []
        for chunk in _PRETOKEN_PATTERN.findall(text):
            cached = self._chunk_cache.get(chunk)
            if cached is None:
                chunk_ids = [BYTE_OFFSET + b for b in chunk.encode("utf-8")]
                cached = self._apply_merges(chunk_ids)
                self._chunk_cache[chunk] = cached
            ids.extend(cached)
        if add_eos:
            ids.append(self.eos_id)
        return ids

    def _apply_merges(self, ids: list[int]) -> list[int]:
        ids = list(ids)
        while len(ids) >= 2:
            pairs_present = set(zip(ids, ids[1:]))
            candidates = [p for p in pairs_present if p in self.merges]
            if not candidates:
                break
            # Lower merged-id == learned earlier == higher priority merge.
            best_pair = min(candidates, key=lambda p: self.merges[p])
            ids = _merge_pair(ids, best_pair, self.merges[best_pair])
        return ids

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        pieces: list[bytes] = []
        for id_ in ids:
            if id_ in self._special_ids:
                if not skip_special_tokens:
                    pieces.append(SPECIAL_TOKENS[id_].encode("utf-8"))
                continue
            pieces.append(self.vocab[id_])
        # errors="replace" guards against text truncated mid-character (see
        # encode_batch's max_length) -- it degrades gracefully instead of raising.
        return b"".join(pieces).decode("utf-8", errors="replace")

    # ---- batch encoding, for the training pipeline ----

    def encode_batch(
        self,
        texts: list[str],
        add_bos: bool = False,
        add_eos: bool = False,
        max_length: int | None = None,
        padding: bool = True,
    ) -> dict[str, list[list[int]]]:
        """Encode a batch of texts into equal-length, model-ready tensors-to-be.

        Returns `input_ids` (right-padded with `pad_id` if `padding=True`) and
        a matching `attention_mask` (1 for real tokens, 0 for padding) so the
        model can ignore padding positions during attention and loss.
        """
        encoded = [self.encode(t, add_bos=add_bos, add_eos=add_eos) for t in texts]
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
        """Save merges to JSON. The base byte vocab is fixed/known, so only
        the learned merges (in order) need to be stored -- load() replays
        them to reconstruct the full vocab."""
        data = {
            "special_tokens": SPECIAL_TOKENS,
            "merges": [[a, b, merged_id] for (a, b), merged_id in self.merges.items()],
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "BPETokenizer":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data["special_tokens"] != SPECIAL_TOKENS:
            raise ValueError("Tokenizer file uses a different special-token set than this code expects")

        vocab: dict[int, bytes] = {BYTE_OFFSET + b: bytes([b]) for b in range(NUM_BYTE_TOKENS)}
        merges: dict[tuple[int, int], int] = {}
        for a, b, merged_id in data["merges"]:
            vocab[merged_id] = vocab[a] + vocab[b]
            merges[(a, b)] = merged_id

        return cls(merges=merges, vocab=vocab)
