# Endpoint versus best-checkpoint scoring — alpaca

| cell | epochs | runs peaking early | mean endpoint penalty | lr* endpoint | lr* best | shift |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| unpacked_350 | 0.22 | 0/7 | 0.0000 | 4.77e-05 | 4.77e-05 | 1.00x |
| packed_350 | 0.98 | 0/7 | 0.0000 | 1.30e-04 | 1.30e-04 | 1.00x |
| unpacked_1600 | 1.01 | 6/7 | 0.0005 | 2.65e-05 | 2.65e-05 | 1.00x |
| packed_1600 | 4.50 | 6/7 | 0.1345 | 2.25e-05 | 3.61e-05 | 1.60x |

The endpoint penalty is `final_val_loss - best_val_loss`: how much a run
gives back after its own best checkpoint. Where it is zero for every run,
the two metrics are the same measurement and the choice between them is free.

## packed_1600 — 4.50 epochs

| max_lr | best checkpoint at | endpoint penalty |
| --- | ---: | ---: |
| 1.0e-05 | 100% of run | 0.0000 |
| 2.0e-05 | 86% of run | 0.0013 |
| 3.0e-05 | 57% of run | 0.0064 |
| 6.0e-05 | 43% of run | 0.0694 |
| 9.0e-05 | 43% of run | 0.1481 |
| 1.5e-04 | 43% of run | 0.2897 |
| 2.5e-04 | 14% of run | 0.4269 |

Penalty rises monotonically with the learning rate: **True**.
That is the bias. The endpoint metric charges the higher learning rates
for overfitting they did after already passing their own minimum, so the
argmin moves down — here by 1.60x — for a reason that has nothing
to do with step size.

## Headline

- Under 1 epoch (packed_350, unpacked_350): no run peaks before its
  final step, so endpoint and best-checkpoint scoring are the same measurement
  and return the same optimum to the digit. The metric choice is free.
- Just over 1 epoch: runs begin to peak early, but by so little that the
  optimum does not move. Differing is not the same as mattering.
- Past ~3 epochs the optimum does move — packed_1600 at 4.5 epochs
  shifts 1.60x, which is larger than this project's worst seed
  spread (1.17x) and comparable to the effects such sweeps are run to
  measure. A sweep whose cells run different numbers of epochs — which any
  sweep over a data-budget-changing intervention does — is comparing cells
  on differently-biased rulers, and has to say which metric it scored on
  and how many epochs each cell ran.

Caveat: `best_val_step` is quantised to the evaluation grid (7 evals per
run), so 'best checkpoint at' is accurate to about 14% of a run. The
penalty itself is not quantised, and it is the quantity the bias depends on.
