# The two Hugging Face Spaces

Both serve the same 124M model this repo pretrained from random initialization
on 2.46B tokens of FineWeb-Edu. They differ in *which checkpoint* -- which is
the whole point, because the difference between them is what
[§10](../README.md#10-instruction-tuning) is about.

| | [`chat/`](chat/) | [`base/`](base/) |
|---|---|---|
| Space | [AshuGPT-chat](https://huggingface.co/spaces/AuthRan/AshuGPT-chat) | [AshuGPT-base](https://huggingface.co/spaces/AuthRan/AshuGPT-base) |
| Checkpoint | `sft_chat/step_1105.pt` | `medium/step_20000.pt` |
| Does what | answers, and holds a multi-turn conversation | continues text |
| UI | `gr.ChatInterface` | single prompt box |

`base/` is the honest control. A visitor who tries both sees the thing this
project spent a week on: the same weights, before and after being taught to
answer rather than continue. Keeping it live costs one Space and makes that
comparison possible instead of merely described.

Neither `model.pt` is committed here -- they are gitignored, rebuilt with
`scripts/export_inference.py`, and pushed to the Spaces with git-LFS.
