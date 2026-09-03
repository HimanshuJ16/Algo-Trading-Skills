# Pre-Flight Checklist

## Data
- [ ] PnL return streams ingested for all active sub-strategy pods, on one shared timestamp index, oldest row first?
- [ ] Market beta / common factor exposure stripped before correlating pod PnL?
- [ ] Column order confirmed to match the `strategy_names` list, and names unique?
- [ ] Non-finite values repaired or dropped upstream (never imputed)?
- [ ] Flat / stale / idle pods excluded explicitly rather than zero-filled?

## Weighting and window
- [ ] `lookback_window` set deliberately (e.g. 30 days) instead of defaulting to full history?
- [ ] Weighting chosen on purpose — `ewma_span=None` for a stable periodic review, an
      integer span ($\ge 2$) for live recomputation — and the span picked against how
      quickly you are willing to act on a convergence?
- [ ] Is `effective_observations` — not the row count — being reported as the sample size
      behind each alert? (Under EWMA it converges to `ewma_span` no matter how deep the history.)
- [ ] `min_observations` calibrated to your noise tolerance, not left at the hard floor of 3?

## Thresholds
- [ ] Correlation thresholds (0.70 / 0.85), `max_avg_correlation_threshold` (0.55) and
      `min_diversification_ratio` (1.20) calibrated against your own pods, and documented
      as policy defaults rather than standards?
- [ ] Capital weights non-negative and matching the pod count?

## Shrinkage
- [ ] Is the shrunken covariance used **only** for downstream optimization, and never compared against a correlation threshold? (A diagonal target scales every off-diagonal $\rho$ by $(1-\delta)$.)
- [ ] Is the shrinkage target the diagonal of $S$ — not the raw identity matrix, which collapses return-scale correlations toward zero?
- [ ] Is the shrinkage intensity documented as a fixed configured constant, not claimed as the Ledoit-Wolf data-estimated optimum?

## Monitoring
- [ ] PnL correlation matrix recomputed continuously, on the **unshrunk** estimate?
- [ ] Pair breaches flagged on $\rho \ge$ threshold — inclusive, one-sided, compared before rounding?
- [ ] Average inter-pod correlation $\bar{\rho}$ monitored independently, so neither it, a
      pair breach, nor the DR shortfall can mask the others?
- [ ] Are `high_correlation_breaches` read alongside $\bar{\rho}$, given that the signed average nets offsetting pairs to zero?
- [ ] Portfolio Diversification Ratio calculated and alerted independently of pair breaches?
- [ ] Persistence across consecutive windows required before capital is cut, given $M(M-1)/2$ simultaneous pair comparisons?
- [ ] $DR = \infty$ / validation exceptions routed to an operator, not swallowed?
- [ ] Is this report wired to an enforcement layer (exposure limits, allocation limits, kill switch) rather than treated as a limit engine in itself?
- [ ] Tail dependence monitored separately (Pearson $\rho$ and $DR$ will not see a crisis convergence)?
