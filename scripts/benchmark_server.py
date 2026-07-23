"""Benchmark a running AshuGPT server's /generate endpoint.

Requires the server to already be running (scripts/serve.py). Fires N
sequential requests and reports throughput -- sequential, not concurrent,
because the server holds one model instance and CPU-bound torch inference
serializes on the GIL anyway; measuring concurrent requests here would
mostly measure queueing, not generation speed.

Usage:
    python scripts/serve.py --checkpoint ... --tokenizer ... &
    python scripts/benchmark_server.py --url http://127.0.0.1:8000 --requests 10 --max-new-tokens 50
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request


def _post_generate(url: str, prompt: str, max_new_tokens: int) -> dict:
    body = json.dumps({"prompt": prompt, "max_new_tokens": max_new_tokens}).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/generate", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--prompt", default="Once upon a time")
    parser.add_argument("--max-new-tokens", type=int, default=50)
    args = parser.parse_args()

    with urllib.request.urlopen(f"{args.url}/health", timeout=10) as resp:
        health = json.load(resp)
    print(f"Server: {health}\n")

    tokens_per_second: list[float] = []
    wall_times: list[float] = []
    for i in range(args.requests):
        t0 = time.perf_counter()
        result = _post_generate(args.url, args.prompt, args.max_new_tokens)
        wall_time = time.perf_counter() - t0
        wall_times.append(wall_time)
        tokens_per_second.append(result["tokens_per_second"])
        print(
            f"request {i + 1:3d}/{args.requests}: "
            f"tokens_generated={result['tokens_generated']:4d}  "
            f"server_time={result['generation_time']:.3f}s  "
            f"wall_time={wall_time:.3f}s  "
            f"tok/s={result['tokens_per_second']:.1f}"
        )

    print(
        f"\ntokens/s: mean={statistics.mean(tokens_per_second):.1f} "
        f"min={min(tokens_per_second):.1f} max={max(tokens_per_second):.1f}"
    )
    print(f"wall time/request: mean={statistics.mean(wall_times):.3f}s (includes HTTP overhead)")


if __name__ == "__main__":
    main()
