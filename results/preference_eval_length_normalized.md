# Preference ranking on `data/preference/hh_test.jsonl`

2,574 held-out pairs. Mean response length: chosen 80 tokens, rejected 73. Implicit rewards are measured against `checkpoints/sft_dolly_packed3e5/step_940.pt`, so its own row is 50% by construction.

| checkpoint | raw accuracy | chosen shorter | chosen longer | per-token accuracy | DPO accuracy | margin | loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `sft` | 46.3% | 92.8% | 8.3% | 54.3% | 50.0% | +0.0000 | 0.6931 |
| `dpo_standard` | 46.4% | 92.9% | 8.4% | 55.2% | 57.1% | +0.0505 | 0.6767 |
| `dpo_ln_b1` | 46.5% | 93.1% | 8.4% | 55.9% | 56.5% | +0.0653 | 0.6782 |
| `dpo_ln_b8` | 46.5% | 93.0% | 8.4% | 54.9% | 57.9% | +0.0230 | 0.6836 |
