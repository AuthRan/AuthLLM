# The two Hugging Face deployments

Both carry the same 124M model this repo pretrained from random initialization
on 2.46B tokens of FineWeb-Edu. They differ in *which checkpoint* -- which is
the whole point, because the difference between them is what
[§10](../README.md#10-instruction-tuning) is about.

| | [`chat/`](chat/) | [`base/`](base/) |
|---|---|---|
| Hosted at | [AshGPT Space](https://huggingface.co/spaces/AuthRan/AshGPT) | [AshuGPT-124M-base](https://huggingface.co/AuthRan/AshuGPT-124M-base) (weights) |
| Checkpoint | `sft_chat/step_1105.pt` | `medium/step_20000.pt` |
| Does what | answers, and holds a multi-turn conversation | continues text |
| UI | `gr.ChatInterface` | single prompt box |

`base/` is the honest control: the same weights before being taught to answer
rather than continue. It is **not** a live Space -- Hugging Face began
requiring a PRO subscription to create new Gradio Spaces, so the base
checkpoint is published as downloadable weights with a model card instead, and
its Gradio app is kept here so the Space can be recreated in one push if that
ever changes.

Neither `model.pt` is committed here -- both are gitignored and rebuilt with
`scripts/export_inference.py`, then uploaded: the chat one to the Space, the
base one to its model repo.
