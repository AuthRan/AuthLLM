# Learning-rate scaling under packing — alpaca (final_val_loss)

## unpacked_350  (1,888 supervised tokens/step, 350 steps, ~0.22 epochs)

| max_lr | 1.0e-05 | 2.0e-05 | 3.0e-05 | 6.0e-05 | 9.0e-05 | 1.5e-04 | 2.5e-04 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| final_val_loss | 2.1885 | 2.1539 | 2.1397 | 2.1338 | 2.1466 | 2.1907 | 2.2770 |

## unpacked_1600  (1,888 supervised tokens/step, 1600 steps, ~1.01 epochs)

| max_lr | 1.0e-05 | 2.0e-05 | 3.0e-05 | 6.0e-05 | 9.0e-05 | 1.5e-04 | 2.5e-04 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| final_val_loss | 2.0973 | 2.0745 | 2.0720 | 2.0973 | 2.1335 | 2.2091 | 2.3149 |

## packed_350  (8,444 supervised tokens/step, 350 steps, ~0.98 epochs)

| max_lr | 1.0e-05 | 2.0e-05 | 3.0e-05 | 6.0e-05 | 9.0e-05 | 1.5e-04 | 2.5e-04 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| final_val_loss | 2.1353 | 2.0911 | 2.0678 | 2.0352 | 2.0217 | 2.0175 | 2.0319 |

## packed_1600  (8,444 supervised tokens/step, 1600 steps, ~4.50 epochs)

| max_lr | 1.0e-05 | 2.0e-05 | 3.0e-05 | 6.0e-05 | 9.0e-05 | 1.5e-04 | 2.5e-04 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| final_val_loss | 2.0421 | 2.0171 | 2.0197 | 2.0845 | 2.1789 | 2.3616 | 2.5703 |

## Optimum per cell

| cell | lr* (per-seed, geometric mean) | seeds | spread (max/min) | bracketed |
| --- | ---: | ---: | ---: | :---: |
| unpacked_350 | 5.00e-05 | 3 | 1.13x | yes |
| unpacked_1600 | 2.81e-05 | 3 | 1.17x | yes |
| packed_350 | 1.37e-04 | 3 | 1.11x | yes |
| packed_1600 | 2.35e-05 | 2 | 1.08x | yes |
| wide_350 | 1.28e-04 | 1 | 1.00x | yes |

## The two effects

| effect | comparison | ratio |
| --- | --- | ---: |
| batch | packed / unpacked at 350 steps | 2.73x |
| batch | packed / unpacked at 1600 steps | 0.83x |
| steps | 350 / 1600 steps, unpacked | 1.78x |
| steps | 350 / 1600 steps, packed | 5.82x |

Excluded from the headline as overfitting-contaminated (>1.5 epochs): packed_1600.

## Headline

- **Batch effect at 350 steps: 2.73x** for a 4.47x token ratio — exponent 0.670 (linear 1.0, square-root 0.5).
  Note this comparison still varies data seen: at a fixed step count the larger
  batch consumes proportionally more of the corpus, and that residual confound
  grows with the packing factor.
- **Step effect (padded): 1.78x** for a 4.57x step ratio — exponent 0.379 in 1/steps.
- **Matched data budget (one epoch each): 4.86x** for a 4.47x packing factor — exponent 1.055.
  This is the number that decides whether inheriting a learning rate is safe.

## The wide-batch control

- `wide_350` (unpacked, accumulation raised until tokens/step match) sits at **1.28e-04**.
- `packed_350` (the same batch reached by packing) sits at **1.30e-04**.
- Both solved on the 1 seed(s) they share (1337).
- Ratio **0.98x**. A statistical-batch rule predicts 1.00x; a rule keyed
  on forward-pass rows rather than supervised tokens predicts 4.47x.

  The two agree. Packing's effect on the optimum is the batch-size effect of
  the examples it fits into a step, not a property of packed representation:
  the same optimum is reached by padding, at ~4.5x the compute.

