# Pre-Flight Checklist — Strategy Decay Diagnosis

## Inputs

- [ ] Peer benchmark index is an appropriate comparator for this strategy's style, and
      is the same index used in the previous diagnosis.
- [ ] Both series are simple per-period returns, net of fees and costs, on the same
      frequency, and `periods_per_year` matches that frequency.
- [ ] Aligned window is free of NaN/Inf, sorted ascending, and free of duplicate index
      labels (the engine raises rather than repairing any of these).
- [ ] At least `rolling_window_days` aligned observations are available.
- [ ] Alignment `warnings` reviewed — how many observations were dropped, and why.

## Statistic

- [ ] Significance comes from the Memmel-corrected Jobson-Korkie statistic, which uses
      the target/peer return correlation — not from the dispersion of overlapping
      rolling Sharpe ratios.
- [ ] $z$ is reported as `None` when it could not be computed, and no classification
      treats a missing statistic as 0.0.
- [ ] The threshold is read as a **one-sided 2.5%** false-positive rate, not "95%
      confidence" in the trading conclusion.
- [ ] Returns are not obviously autocorrelated or heavy-tailed; if they are, the
      closed-form p-value over-states significance and a studentized time-series
      bootstrap is required before acting.
- [ ] Multiple-testing exposure across the book has been accounted for.

## Classification

- [ ] `IDIOSYNCRATIC_ALPHA_DECAY` fires only when the target's **own** annualized Sharpe
      is below the health threshold while the peer benchmark is above it.
- [ ] A strategy above the health threshold is never routed to `DECOMMISSION_OR_RECODE`
      on relative underperformance alone.
- [ ] `MARKET_WIDE_REGIME_SHIFT` requires both target and peer below the threshold.
- [ ] A constant return series reports `INCONCLUSIVE` with an undefined Sharpe ratio,
      not a Sharpe of 0.0 classified as impaired.

## Before acting

- [ ] Execution causes (slippage, routing, fees, borrow) ruled out as the source of the
      Sharpe fall.
- [ ] Health threshold (default annualized Sharpe 0.50) is a documented house default,
      reviewed and defensible for this mandate — it is not an external standard.
- [ ] Full report archived: strategy and peer ids, observation count, both Sharpe
      ratios, $z$, p-value, correlation, thresholds, and all warnings.
