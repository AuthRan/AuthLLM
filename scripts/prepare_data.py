"""Download a pretraining corpus, tokenize it in parallel, and write it to
disk as uint16 token shards.

SPEC.md has listed this script in its command table since the beginning; it
was never built, because CPU-scale training only ever needed the in-memory
path in `ashugpt/data/dataset.py` (one text file -> one tensor). That path
does not survive contact with a real corpus: it calls `Path.read_text()` on
the whole file and materializes every token as int64. At 5B tokens that is
~10GB of text held as one Python `str` plus 40GB of int64 tensor.

This script produces the on-disk format that `TokenizedDataset` memory-maps
instead:

    data/fineweb_edu_5B/
        manifest.json        # tokenizer, shard list, token counts
        val_000000.npy       # uint16, the first shard -- held out
        train_000000.npy     # uint16
        train_000001.npy
        ...

uint16 is not an optimization detail, it is the reason this fits: the
tokenizer's vocabulary is 50259 entries, comfortably inside uint16's 65536,
so every token costs 2 bytes instead of int64's 8. A 5B-token corpus is 10GB
on disk rather than 40GB, and memory-mapping means resident memory stays
flat regardless of corpus size.

Documents are concatenated into one continuous stream with an `<|endoftext|>`
token after each -- the standard GPT-2 pretraining layout, and the reason
`TiktokenBPETokenizer` aliases `bos_id` to that same token (see its
docstring).

Usage:
    python scripts/prepare_data.py --output data/fineweb_edu_5B --target-tokens 5e9
    python scripts/prepare_data.py --output data/tiny --dataset tinystories --target-tokens 5e7

Streaming means only the data actually needed is downloaded: the script stops
pulling from the remote dataset as soon as --target-tokens is reached.

Not resumable. A streaming dataset has no cheap seek -- restarting mid-corpus
would re-download everything skipped -- so an interrupted run starts over.
Shards are written incrementally as they fill, though, so an interrupted run
leaves valid (just smaller) data behind: re-point --target-tokens at what you
actually got and the manifest can be regenerated.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

from ashugpt.tokenizer import TiktokenBPETokenizer

# Registry of corpora this script knows how to stream. Each entry is
# (hf_dataset_path, hf_config_name, text_column).
DATASETS = {
    "fineweb-edu": ("HuggingFaceFW/fineweb-edu", "sample-10BT", "text"),
    "tinystories": ("roneneldan/TinyStories", None, "text"),
    "openwebtext": ("Skylion007/openwebtext", None, "text"),
}

DEFAULT_SHARD_TOKENS = 100_000_000  # 200MB per uint16 shard

_worker_tokenizer: TiktokenBPETokenizer | None = None


def _init_worker() -> None:
    """Each worker process builds its own tokenizer once, at startup, rather
    than per batch -- constructing it means loading GPT-2's merge table."""
    global _worker_tokenizer
    _worker_tokenizer = TiktokenBPETokenizer()


def _tokenize_batch(texts: list[str]) -> np.ndarray:
    """Tokenize a batch of documents into one flat uint16 array, with an
    eos token terminating each document."""
    assert _worker_tokenizer is not None, "worker was not initialized"
    tok = _worker_tokenizer
    encoded = tok.encode_batch(texts, add_eos=True, padding=False)["input_ids"]

    total = sum(len(ids) for ids in encoded)
    out = np.empty(total, dtype=np.uint16)
    offset = 0
    for ids in encoded:
        out[offset : offset + len(ids)] = ids
        offset += len(ids)
    return out


def _stream_documents(dataset: str, batch_size: int):
    """Yield lists of `batch_size` document strings from a streaming HF
    dataset. Streaming (rather than a full download) is what lets
    --target-tokens cap the bytes actually pulled over the network."""
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise ImportError(
            "scripts/prepare_data.py needs the optional `datasets` dependency (pip install datasets)."
        ) from e

    path, config, column = DATASETS[dataset]
    stream = load_dataset(path, name=config, split="train", streaming=True)

    batch: list[str] = []
    for record in stream:
        text = record[column]
        if not text:
            continue
        batch.append(text)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _shard_path(output_dir: Path, index: int) -> Path:
    """Shard 0 is the validation shard; everything after it is training data.

    Holding out a whole contiguous shard (rather than a random sample of
    windows) is deliberate: validation windows must not be able to overlap
    training windows, or held-out perplexity is measured against text the
    model has partly memorized. `split_train_val()` in dataset.py makes the
    same argument for the in-memory path.
    """
    prefix = "val" if index == 0 else "train"
    number = index if index == 0 else index - 1
    return output_dir / f"{prefix}_{number:06d}.npy"


def prepare(
    dataset: str,
    output_dir: Path,
    target_tokens: int,
    shard_tokens: int,
    num_workers: int,
    doc_batch_size: int,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    buffer = np.empty(shard_tokens, dtype=np.uint16)
    buffer_used = 0
    shard_index = 0
    total_tokens = 0
    shards: list[dict] = []
    start = time.time()

    def flush_shard(count: int) -> None:
        """Write the first `count` tokens of the buffer out as one shard."""
        nonlocal shard_index
        path = _shard_path(output_dir, shard_index)
        np.save(path, buffer[:count])
        shards.append({"file": path.name, "tokens": int(count)})
        elapsed = time.time() - start
        print(
            f"  wrote {path.name}: {count:,} tokens "
            f"({total_tokens:,} total, {total_tokens / max(elapsed, 1e-9) / 1e6:.2f}M tok/s)"
        )
        shard_index += 1

    print(f"Streaming '{dataset}' -> {output_dir} (target {target_tokens:,} tokens, {num_workers} workers)")

    def consume(tokens: np.ndarray) -> None:
        """Append one worker's tokenized batch to the shard buffer, flushing
        whenever it fills. A batch can straddle a shard boundary, so it is
        copied in as many pieces as needed rather than assumed to fit."""
        nonlocal buffer_used
        consumed = 0
        while consumed < len(tokens):
            space = shard_tokens - buffer_used
            take = min(space, len(tokens) - consumed)
            buffer[buffer_used : buffer_used + take] = tokens[consumed : consumed + take]
            buffer_used += take
            consumed += take
            if buffer_used == shard_tokens:
                flush_shard(shard_tokens)
                buffer_used = 0

    batches = _stream_documents(dataset, doc_batch_size)
    pool = mp.Pool(num_workers, initializer=_init_worker)
    try:
        # Deliberately NOT pool.imap(). imap hands the generator to Pool's
        # internal feeder thread, which then sits inside a network-backed HF
        # streaming iterator that owns background threads of its own. Breaking
        # out early (which --target-tokens guarantees) leaves that feeder stuck
        # mid-generator: it cannot be closed ("generator already executing")
        # and the interpreter crashes during finalization
        # ("PyGILState_Release: auto-releasing thread-state").
        #
        # apply_async keeps generator consumption on the main thread, where
        # breaking out is well defined. The bounded window below preserves
        # what imap was there for -- workers tokenize while later batches are
        # still downloading -- without handing the generator to a thread.
        max_pending = num_workers * 2
        pending: deque = deque()
        reached_target = False

        for batch in batches:
            pending.append(pool.apply_async(_tokenize_batch, (batch,)))
            if len(pending) < max_pending:
                continue
            tokens = pending.popleft().get()
            total_tokens += len(tokens)
            consume(tokens)
            if total_tokens >= target_tokens:
                reached_target = True
                break

        if not reached_target:  # stream ended before the target -- drain what's in flight
            while pending:
                tokens = pending.popleft().get()
                total_tokens += len(tokens)
                consume(tokens)
                if total_tokens >= target_tokens:
                    break
    finally:
        batches.close()  # safe now: only this thread ever iterated it
        pool.terminate()
        pool.join()

    if buffer_used > 0:
        flush_shard(buffer_used)

    manifest = {
        "dataset": dataset,
        "tokenizer": {"type": "tiktoken", "encoding": "gpt2", "vocab_size": TiktokenBPETokenizer().vocab_size},
        "total_tokens": int(total_tokens),
        "shards": shards,
        "val_shard": shards[0]["file"] if shards else None,
        "train_shards": [s["file"] for s in shards[1:]],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    elapsed = time.time() - start
    print(f"Done: {total_tokens:,} tokens across {len(shards)} shards in {elapsed / 60:.1f} min")
    if len(shards) < 2:
        print(
            "WARNING: only one shard was written, so it became the validation shard and "
            "no training data remains. Raise --target-tokens or lower --shard-tokens."
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="fineweb-edu")
    parser.add_argument("--output", type=Path, required=True, help="Directory to write shards + manifest into")
    parser.add_argument(
        "--target-tokens",
        type=float,
        default=5e9,
        help="Stop after approximately this many tokens (accepts 5e9 notation). Default 5e9.",
    )
    parser.add_argument("--shard-tokens", type=int, default=DEFAULT_SHARD_TOKENS)
    parser.add_argument(
        "--num-workers",
        type=int,
        default=max(1, mp.cpu_count() - 2),
        help="Tokenizer processes. Defaults to cpu_count-2, leaving headroom for the download thread.",
    )
    parser.add_argument("--doc-batch-size", type=int, default=256, help="Documents handed to a worker at a time")
    args = parser.parse_args()

    prepare(
        dataset=args.dataset,
        output_dir=args.output,
        target_tokens=int(args.target_tokens),
        shard_tokens=args.shard_tokens,
        num_workers=args.num_workers,
        doc_batch_size=args.doc_batch_size,
    )

    # Exit without running interpreter finalization, on purpose.
    #
    # `datasets`' streaming backend reaches HTTP through fsspec, which runs an
    # asyncio event loop on a background thread. When this script stops
    # consuming the stream early -- which --target-tokens guarantees it will --
    # that thread is still alive at shutdown, and CPython's finalizer trips
    # over it: "PyGILState_Release: thread state must be current when
    # releasing", abort, exit code 134. The abort happens *after* all work is
    # done, but a 134 from an unattended multi-hour data prep is
    # indistinguishable from a real failure, and would poison any script that
    # checks the exit status.
    #
    # This is safe here specifically because nothing is left buffered: np.save
    # closed every shard file and write_text closed the manifest before
    # prepare() returned. There is no output that finalization would flush.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
