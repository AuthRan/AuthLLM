# Preference ranking on `data/preference/hh_test.jsonl`

2,574 held-out pairs. Mean response length: chosen 80 tokens, rejected 73. Implicit rewards are measured against `checkpoints/sft_dolly_packed3e5/step_940.pt`, so its own row is 50% by construction.

| checkpoint | raw accuracy | chosen shorter | chosen longer | per-token accuracy | DPO accuracy | margin | loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `sft` | 46.3% | 92.8% | 8.3% | 54.3% | 50.0% | +0.0000 | 0.6931 |
| `dpo_1e6` | 46.4% | 92.9% | 8.4% | 55.2% | 57.1% | +0.0505 | 0.6767 |
| `dpo_5e6` | 46.6% | 93.0% | 8.6% | 56.1% | 58.4% | +0.1083 | 0.6669 |
| `dpo_2e5` | 46.8% | 92.7% | 9.1% | 56.6% | 59.9% | +0.1774 | 0.6655 |
