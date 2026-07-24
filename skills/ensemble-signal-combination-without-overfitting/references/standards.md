# Financial ML Standards — ensemble-signal-combination-without-overfitting

| Weighting Method | Constraint / Regularization | Primary Strength |
|---|---|---|
| Equal Weighting (1/N) | Zero free parameters | Robust baseline, zero overfitting risk |
| Inverse Variance | $w_i \propto 1/\sigma_i^2$ | Downweights volatile sub-signals |
| Shrunk NNLS | $w_i \ge 0$, $1/N$ Shrinkage ($\lambda = 0.5$) | Prevents negative bets, blends robustness |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with multi-model risk management, ensemble signal aggregation, and quantitative alpha combination standards.
