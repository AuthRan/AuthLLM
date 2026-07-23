"""The only place the API layer touches AshuGPT's actual model/tokenizer/
generation code -- ashugpt/api/app.py never imports ashugpt.model,
ashugpt.tokenizer, or ashugpt.inference.generate directly, only this
module. Keeps the HTTP layer swappable (or testable in isolation) without
touching model code, and keeps model code with zero knowledge that an API
exists at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import torch

from ashugpt.inference.generate import generate
from ashugpt.tokenizer import BPETokenizer
from ashugpt.training.checkpoint import load_model_for_inference


@dataclass
class GenerateResult:
    generated_text: str
    tokens_generated: int
    generation_time: float
    tokens_per_second: float


class InferenceService:
    """Holds one loaded model + tokenizer in memory. Constructed once at
    server startup (see app.py's lifespan handler) and reused for every
    request -- loading (reading the checkpoint file, constructing the
    nn.Module, moving weights into it) happens exactly once, not per call."""

    def __init__(self, model: torch.nn.Module, tokenizer: BPETokenizer, checkpoint_path: str) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.checkpoint_path = checkpoint_path
        self.model.eval()

    @classmethod
    def load(cls, checkpoint_path: str | Path, tokenizer_path: str | Path) -> InferenceService:
        tokenizer = BPETokenizer.load(tokenizer_path)
        model = load_model_for_inference(checkpoint_path)
        return cls(model=model, tokenizer=tokenizer, checkpoint_path=str(checkpoint_path))

    def generate(
        self,
        prompt: str,
        max_new_tokens: int,
        temperature: float,
        top_k: int | None,
        top_p: float | None,
    ) -> GenerateResult:
        """Raises ValueError for requests the model itself rejects (e.g.
        prompt + max_new_tokens exceeding context_length, or an invalid
        temperature/top_k/top_p) -- the caller (app.py) maps that to a 400,
        distinct from an actual server-side failure."""
        input_ids = torch.tensor([self.tokenizer.encode(prompt, add_bos=True)])
        prompt_len = input_ids.shape[1]

        start = time.perf_counter()
        output_ids = generate(
            self.model,
            input_ids,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            eos_id=self.tokenizer.eos_id,
        )
        elapsed = time.perf_counter() - start

        tokens_generated = output_ids.shape[1] - prompt_len
        generated_text = self.tokenizer.decode(output_ids[0].tolist())
        tokens_per_second = tokens_generated / elapsed if elapsed > 0 else float("inf")

        return GenerateResult(
            generated_text=generated_text,
            tokens_generated=tokens_generated,
            generation_time=elapsed,
            tokens_per_second=tokens_per_second,
        )

    @property
    def parameter_count(self) -> int:
        return self.model.num_parameters()
