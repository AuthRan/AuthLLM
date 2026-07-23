"""Run the AshuGPT inference server locally.

Usage:
    python scripts/serve.py --checkpoint checkpoints/demo/step_150.pt --tokenizer tokenizer.json
    python scripts/serve.py --checkpoint ... --tokenizer ... --host 0.0.0.0 --port 8080

Equivalent without this wrapper (e.g. for a container, where env vars are
the natural way to configure a service):
    ASHUGPT_CHECKPOINT=... ASHUGPT_TOKENIZER=... uvicorn ashugpt.api.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from ashugpt.api.app import CHECKPOINT_ENV_VAR, TOKENIZER_ENV_VAR


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    os.environ[CHECKPOINT_ENV_VAR] = str(args.checkpoint)
    os.environ[TOKENIZER_ENV_VAR] = str(args.tokenizer)

    uvicorn.run("ashugpt.api.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
