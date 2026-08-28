# Pre-Flight Checklist

## Input integrity

- [ ] Are all strategy return series synchronized on one timestamp index, oldest first, with market beta and shared factor exposure already stripped?
- [ ] Does the panel reject — rather than impute — NaN/infinite values, zero-variance (flat/stale/idle) strategies, duplicate column names, and non-numeric columns?
- [ ] Are idle strategies excluded from scope as an explicit decision, never zero-filled?

## Estimation

- [ ] Is EWMA weighting active, with `ewma_span` chosen against how quickly you are willing to act on a convergence (and $\ge 2$)?
- [ ] Is `effective_observations` ($1/\sum w_t^2$, converging to `ewma_span`) — not the row count — being reported as the sample size behind each alert?
- [ ] Does the panel clear `min_observations`, set above the hard floor of 3?

## Shrinkage

- [ ] Is the shrunken covariance used **only** for downstream optimization, and never compared against a correlation threshold? (A diagonal target scales every off-diagonal $\rho$ by $(1-\delta)$.)
- [ ] Is the shrinkage target the diagonal of $S$ — not the raw identity matrix, which collapses return-scale correlations toward zero?
- [ ] Is the shrinkage intensity documented as a fixed configured constant, not claimed as the Ledoit-Wolf data-estimated optimum?

## Alerting

- [ ] Are pair alerts triggered on the **unshrunk** correlation estimate at $\rho \ge$ `high_correlation_threshold`, inclusive, one-sided, compared before rounding?
- [ ] Is average inter-strategy correlation $\bar{\rho}$ monitored independently, so neither it nor a single pair alert can mask the other?
- [ ] Are `high_correlation_pairs` read alongside $\bar{\rho}$, given that the signed average nets offsetting pairs to zero?
- [ ] Are the 0.70 / 0.55 thresholds calibrated against your own strategies' empirical EWMA correlation distribution, and documented as internal policy rather than an external standard?
- [ ] Is persistence across consecutive recomputations required before cutting capital, given $N(N-1)/2$ simultaneous pair comparisons?

## Downstream

- [ ] Is this report wired to an enforcement layer (exposure limits, allocation limits, kill switch) rather than treated as a limit engine in itself?
- [ ] Is tail dependence covered separately, given that Pearson $\rho$ says nothing about left-tail co-movement?
