# Preference ranking on `data/preference/hh_test.jsonl`

2,574 held-out pairs. Mean response length: chosen 80 tokens, rejected 73. Implicit rewards are measured against `checkpoints/sft_dolly_packed3e5/step_940.pt`, so its own row is 50% by construction.

| checkpoint | raw accuracy | chosen shorter | chosen longer | per-token accuracy | DPO accuracy | margin | loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `sft` | 46.3% | 92.8% | 8.3% | 54.3% | 50.0% | +0.0000 | 0.6931 |
| `dpo_400` | 46.4% | 92.9% | 8.4% | 55.2% | 57.1% | +0.0505 | 0.6767 |
| `dpo_598` | 46.5% | 92.8% | 8.6% | 55.7% | 57.3% | +0.0792 | 0.6719 |
| `dpo_1196` | 46.5% | 92.8% | 8.6% | 55.7% | 58.4% | +0.0921 | 0.6681 |
| `dpo_1495` | 46.5% | 92.8% | 8.6% | 55.7% | 58.4% | +0.0935 | 0.6675 |
