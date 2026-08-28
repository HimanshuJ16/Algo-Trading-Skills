# Pre-Flight / Sign-off Checklist — real-time-liquidity-risk-monitoring

## Input data
- [ ] One observation per symbol; lots are netted before the audit (duplicates raise).
- [ ] Spreads are in **price units**, ADV and depth in **share/contract units**, prices and VaR in one currency.
- [ ] `adv`, `current_price`, `normal_spread`, `normal_l2_depth` are all strictly positive; spreads and depths are non-negative.
- [ ] Non-finite values (`NaN`/`inf`) are rejected at the boundary, not clamped — a `NaN` metric raises no flag and reports a healthy book.
- [ ] The ADV supplied is the one expected **in the regime being monitored**, not a calm-market trailing average.
- [ ] Observation recency is verified upstream — the engine has no timestamp and no staleness check.

## Metrics
- [ ] Days to Liquidate computed per symbol at the configured participation cap, using position **magnitude** (shorts included).
- [ ] `max_days_to_liquidate` is read as the worst symbol, not a portfolio average.
- [ ] Bid-ask spread spikes flagged at $\ge 2.0\times$ each symbol's **own** baseline.
- [ ] L2 depth drops flagged at $\ge 50\%$ against that symbol's baseline.
- [ ] It is understood that all three thresholds are **inclusive** — exactly at the limit is a breach.

## Liquidity-Adjusted VaR
- [ ] `baseline_var_usd` is this portfolio's own mid-price VaR, at a stated confidence level, horizon, and currency.
- [ ] `spread_cost_usd` vs `impact_cost_usd` reviewed: if the impact half dominates, the L-VaR is mostly a function of $k$.
- [ ] `market_impact_coeff_per_day` has been calibrated against realized transaction costs, or deliberately set to $0.0$ — the $0.10$ default is a placeholder, not a recommendation.
- [ ] It is understood that the half-spread term uses the snapshot spread with tail scaler $a = 0$ (BDSS Eq. 4), so it understates tail exogenous liquidity cost.

## Scope and governance
- [ ] Thresholds ($2.0$ days, $2.0\times$, $50\%$) have been calibrated and the rationale recorded — they are library defaults, not regulatory limits.
- [ ] `NO_POSITIONS` is not treated as a pass.
- [ ] Funding liquidity (cash, collateral, margin) is covered by a separate control — this skill measures market liquidity only.
- [ ] An enforcement path exists downstream (kill switch / unwind scheduler) — this engine only reports.
- [ ] A logging handler is configured in the host application so `WARNING` breaches are actually delivered.

## Testing
- [ ] Automated Testing: Run `python scripts/test_real_time_liquidity_monitor.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
