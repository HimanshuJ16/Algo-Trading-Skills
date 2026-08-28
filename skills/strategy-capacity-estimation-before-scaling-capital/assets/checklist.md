# Pre-Flight Checklist — Strategy Capacity Estimation

## Inputs

- [ ] Is `daily_turnover_pct` **one-way** notional, matching the **half**-spread charged on it?
- [ ] Is `avg_daily_volume_usd` in the same currency as AUM, and honest about the illiquid tail rather than a flattering blended average?
- [ ] Is `annual_gross_return_pct` an excess return, or has `risk_free_rate_pct` been set? (Leaving it at $0.0$ on a total return adds $r_f/\sigma$ to every Sharpe.)
- [ ] Are returns, volatilities, and turnover fractions, while `max_participation_rate_pct` is a percentage ($5.0$ = 5%)?

## Model

- [ ] Is `impact_gamma` **calibrated to realized slippage**, not left at the $0.5$ default — the optimistic end of the empirical $0.5$–$1.0$ range?
- [ ] If it is uncalibrated, has the estimate been run at $Y = 1.0$ as well, with the lower capacity taken as the working number?
- [ ] Is the model correctly cited as the **square-root law** (Torre/BARRA 1997; Grinold and Kahn 1999), not as Almgren-Chriss, which is a *linear*-impact model?
- [ ] Is $252$ trading days the right annualisation for this venue? (Crypto is not.)

## Grid

- [ ] Is `capacity_resolution_usd` fine enough that the grid step is immaterial to the allocation decision?
- [ ] Is `max_capacity_aum_usd > 0`? A $0.0$ result means "below one grid step", not "exactly zero".

## Result

- [ ] Is `search_range_exhausted` **False**? If True, the reported capacity is the search ceiling you chose, not a measured limit — widen `max_search_aum_usd` and re-run.
- [ ] Has the `limiting_factor` been read and acted on — `ADV_PARTICIPATION_LIMIT` (liquidity), `MIN_SHARPE_BREACH` (impact drag), or `BELOW_MIN_SHARPE_AT_ALL_SIZES` (strategy quality, not capacity)?
- [ ] Is the allocation drawn from `optimal_sharpe_capacity_aum_usd` (feasible) and **not** from `unconstrained_max_pnl_aum_usd` (diagnostic, routinely far above the safe limit)?
- [ ] Is `max_capacity_net_sharpe` at or above the gate, and is that gate the one your allocation policy actually requires?

## Before deploying

- [ ] Is it understood that gross alpha is assumed **invariant to AUM**, so this capacity is an **upper bound** — a ceiling to stay under, not a target to reach?
- [ ] Has per-name liquidity been checked separately (`liquidity-adjusted-position-sizing`), since aggregate ADV hides where capacity really binds?
- [ ] Is capital being ramped in stages rather than deployed to the estimate in one step (`incremental-capital-deployment-for-new-strategies`)?
- [ ] Is the $5\%$ participation cap understood as a **practitioner convention**, not a regulatory limit, and has the actual applicable regime been checked for this jurisdiction and entity type?
