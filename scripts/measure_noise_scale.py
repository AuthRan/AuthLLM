"""Measure the gradient noise scale of an instruction-tuning setup.

Section 4.6 of the paper reports that the packing learning-rate exponent moves
with the scale of the run and declines to say why. Li et al. (2024) put the
dependence somewhere specific: the optimal learning rate for Adam is
non-monotone in batch size, rising and then falling, with the turn at the
gradient noise scale `B_noise`. If that is what our exponent is tracking, then
the settings whose exponent differs should differ in where their batch sits
relative to their own noise scale -- and that is measurable rather than
arguable.

## What is computed

For a loss that is a token-mean over `T` supervised tokens, the expected squared
gradient norm is

    E|G_T|^2  =  |G|^2  +  tr(S) / T

with `|G|^2` the squared norm of the true gradient and `tr(S)` the trace of the
per-token gradient covariance (McCandlish et al., 2018). Estimating `|G_T|^2` at
several token counts and fitting a straight line against `1/T` gives both, and

    B_simple = tr(S) / |G|^2

is the noise scale in the same unit the paper measures batches in -- supervised
tokens per optimizer step -- so it can be compared against them directly.

The two-point estimator in the original paper is a special case of this fit. We
use several points because a two-point estimate of a ratio of two small
differences is badly conditioned, and because the residuals of the fit say
whether the linear form holds at all here.

## Usage

    python scripts/measure_noise_scale.py \\
        --model configs/model/medium.yaml \\
        --init-from checkpoints/medium/step_20000.pt \\
        --data data/sft/alpaca.jsonl --packed --repeats 24

`--packed` measures the packed dataset, which is the one whose batch the paper's
packed arm uses; without it the padded dataset is measured. The two differ in
which examples land in a batch together, not in the objective, so a difference
between them is itself informative.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ashugpt.config import load_model_config  # noqa: E402
from ashugpt.data.instruction import (  # noqa: E402
    IGNORE_INDEX,
    InstructionDataset,
    InstructionExample,
    PackedInstructionDataset,
)
from ashugpt.model.gpt import AshuGPT  # noqa: E402
from ashugpt.tokenizer.tiktoken_bpe import TiktokenBPETokenizer  # noqa: E402


def read_jsonl(path: Path) -> list[InstructionExample]:
    out = []
    for line in path.open(encoding="utf-8"):
        row = json.loads(line)
        out.append(InstructionExample(instruction=row["instruction"],
                                      input=row.get("input", ""),
                                      output=row["output"]))
    return out


def grad_sq_norm(model, micro_batches, device) -> tuple[float, int]:
    """Squared norm of the token-mean gradient over a batch, and its token count.

    The loss is the same one training uses -- token-mean cross-entropy over
    supervised positions -- so the gradient here is the gradient the optimizer
    would have seen from this batch.

    The batch arrives split into micro-batches and is accumulated, which is what
    lets this reach the sizes the paper's steps actually use: 8,444 supervised
    tokens is about 143 Alpaca examples, and 143 sequences of 512 tokens will not
    fit in one backward pass on an 11 GB card. A first attempt without this
    capped out at 443 supervised tokens against the 1,888 and 8,444 the paper
    measures, and every fit came back with a negative intercept -- the true
    gradient was below the resolution of an extrapolation from that far away.

    Accumulation has to be weighted. Each micro-batch's loss is a token-mean over
    its own tokens, so summing the gradients would average the micro-batches
    rather than the tokens whenever they carry different counts, which they
    always do. Scaling each backward by its token count and dividing the total by
    the sum gives the token-mean over the union, which is the quantity a single
    large batch would have produced.
    """
    model.zero_grad(set_to_none=True)
    total_tokens = 0
    for batch in micro_batches:
        tensors = [t.to(device) for t in batch]
        if len(tensors) == 4:
            input_ids, labels, segment_ids, position_ids = tensors
            output = model(input_ids, labels=labels,
                           segment_ids=segment_ids, position_ids=position_ids)
        else:
            input_ids, labels = tensors
            output = model(input_ids, labels=labels)
        count = int((labels != IGNORE_INDEX).sum())
        if count == 0:
            continue
        (output.loss * count).backward()
        total_tokens += count
    if total_tokens == 0:
        return 0.0, 0
    total = 0.0
    for p in model.parameters():
        if p.grad is not None:
            total += float((p.grad.detach().double() / total_tokens).pow(2).sum())
    model.zero_grad(set_to_none=True)
    return total, total_tokens


def collate(dataset, indices):
    items = [dataset[i] for i in indices]
    return [torch.stack([item[k] for item in items]) for k in range(len(items[0]))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--init-from", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--packed", action="store_true")
    parser.add_argument("--seq-len", type=int, default=512)
    parser.add_argument("--fit-from", type=float, default=0.0,
                        help="Only fit points whose mean supervised-token count "
                             "is at least this. E|G_T|^2 = |G|^2 + tr(S)/T is a "
                             "large-batch approximation and it does not hold "
                             "down to a handful of tokens; fitting it over a "
                             "range where it does not returns a negative "
                             "intercept and a high R^2 at the same time")
    parser.add_argument("--micro-batch", type=int, default=8,
                        help="Sequences per backward pass; a size larger than "
                             "this is accumulated over several, so the reachable "
                             "batch is bounded by patience and not by memory")
    parser.add_argument("--sizes", nargs="+", type=int, default=[1, 2, 4, 8, 16, 32],
                        help="Rows per batch; token counts follow from them")
    parser.add_argument("--repeats", type=int, default=24,
                        help="Batches averaged at each size")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    tokenizer = TiktokenBPETokenizer()
    examples = read_jsonl(args.data)
    random.Random(args.seed).shuffle(examples)
    train = examples[max(1, int(len(examples) * 0.02)):]

    build = PackedInstructionDataset if args.packed else InstructionDataset
    dataset = build(train, tokenizer, seq_len=args.seq_len)

    model = AshuGPT(load_model_config(args.model))
    state = torch.load(args.init_from, map_location="cpu", weights_only=True)
    model.load_state_dict(state["model_state_dict"])
    model.to(args.device).train()

    rng = random.Random(args.seed)
    points: list[tuple[float, float]] = []   # (1/tokens, mean |G|^2)
    print(f"{'rows':>6}{'mean tokens':>13}{'mean |G|^2':>14}{'batches':>9}")
    print("-" * 44)
    for size in args.sizes:
        norms, tokens = [], []
        for _ in range(args.repeats):
            idx = [rng.randrange(len(dataset)) for _ in range(size)]
            chunks = [collate(dataset, idx[i:i + args.micro_batch])
                      for i in range(0, len(idx), args.micro_batch)]
            n, t = grad_sq_norm(model, chunks, args.device)
            if t == 0:
                continue
            norms.append(n)
            tokens.append(t)
        if not norms:
            continue
        mean_tokens = sum(tokens) / len(tokens)
        mean_norm = sum(norms) / len(norms)
        points.append((1.0 / mean_tokens, mean_norm))
        print(f"{size:>6}{mean_tokens:>13.1f}{mean_norm:>14.6e}{len(norms):>9}")

    fitted = [(x, y) for x, y in points if 1.0 / x >= args.fit_from]
    if args.fit_from:
        print(f"\nfitting the {len(fitted)} of {len(points)} points at or above "
              f"{args.fit_from:,.0f} supervised tokens")
    points = fitted
    if len(points) < 3:
        raise SystemExit("not enough points to fit")

    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    slope = sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x, _ in points)
    intercept = my - slope * mx
    residuals = [y - (intercept + slope * x) for x, y in points]
    ss_tot = sum((y - my) ** 2 for _, y in points)
    ss_res = sum(r * r for r in residuals)

    worst = max(abs(y - (intercept + slope * x)) / y for x, y in points)
    print()
    print(f"largest relative residual = {worst:.1%}")
    if worst > 0.25:
        print("the two-parameter model does not describe these points; R^2 will")
        print("still look high if the small-batch end carries most of the variance")
    print(f"|G|^2  (intercept) = {intercept:.6e}")
    print(f"tr(S)  (slope)     = {slope:.6e}")
    print(f"R^2 of the fit     = {1 - ss_res / ss_tot:.4f}")
    if intercept <= 0:
        print("\nintercept is not positive: the fit does not identify a noise scale here.")
        print("More repeats, or a wider spread of batch sizes, would be needed.")
        return
    print()
    print(f"B_simple = tr(S)/|G|^2 = {slope / intercept:,.0f} supervised tokens")
    print()
    print("Compare against the paper's steps, which carry 411 to 10,119 supervised")
    print("tokens depending on the cell. A batch far below B_simple is in the regime")
    print("where Li et al. predict square-root scaling; near or above it is not.")


if __name__ == "__main__":
    main()
