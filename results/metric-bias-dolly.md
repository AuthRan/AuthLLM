# Endpoint versus best-checkpoint scoring — dolly

| cell | epochs | runs peaking early | mean endpoint penalty | lr* endpoint | lr* best | shift |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| unpacked_136 | 0.32 | 0/7 | 0.0000 | 4.87e-05 | 4.87e-05 | 1.00x |
| packed_136 | 0.92 | 0/7 | 0.0000 | 7.98e-05 | 7.98e-05 | 1.00x |
| unpacked_430 | 1.00 | 0/7 | 0.0000 | 3.61e-05 | 3.61e-05 | 1.00x |
| packed_430 | 2.92 | 5/7 | 0.0877 | 2.44e-05 | 3.14e-05 | 1.29x |

The endpoint penalty is `final_val_loss - best_val_loss`: how much a run
gives back after its own best checkpoint. Where it is zero for every run,
the two metrics are the same measurement and the choice between them is free.

## packed_430 — 2.92 epochs

| max_lr | best checkpoint at | endpoint penalty |
| --- | ---: | ---: |
| 1.0e-05 | 100% of run | 0.0000 |
| 2.0e-05 | 100% of run | 0.0000 |
| 3.0e-05 | 57% of run | 0.0032 |
| 6.0e-05 | 28% of run | 0.0362 |
| 9.0e-05 | 28% of run | 0.0902 |
| 1.5e-04 | 28% of run | 0.1882 |
| 2.5e-04 | 28% of run | 0.2962 |

Penalty rises monotonically with the learning rate: **True**.
That is the bias. The endpoint metric charges the higher learning rates
for overfitting they did after already passing their own minimum, so the
argmin moves down — here by 1.29x — for a reason that has nothing
to do with step size.

## Headline

- Under 1 epoch (packed_136, unpacked_136): no run peaks before its
  final step, so endpoint and best-checkpoint scoring are the same measurement
  and return the same optimum to the digit. The metric choice is free.
- Just over 1 epoch: runs begin to peak early, but by so little that the
  optimum does not move. Differing is not the same as mattering.
- Past ~3 epochs the optimum does move — packed_430 at 2.9 epochs
  shifts 1.29x, which is larger than this project's worst seed
  spread (1.17x) and comparable to the effects such sweeps are run to
  measure. A sweep whose cells run different numbers of epochs — which any
  sweep over a data-budget-changing intervention does — is comparing cells
  on differently-biased rulers, and has to say which metric it scored on
  and how many epochs each cell ran.

Caveat: `best_val_step` is quantised to the evaluation grid (7 evals per
run), so 'best checkpoint at' is accurate to about 14% of a run. The
penalty itself is not quantised, and it is the quantity the bias depends on.
