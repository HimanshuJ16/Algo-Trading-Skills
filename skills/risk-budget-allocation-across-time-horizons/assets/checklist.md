# Pre-Flight / Sign-off Checklist — risk-budget-allocation-across-time-horizons

## Horizon definitions
- [ ] Every horizon bucket has an explicit `allocated_risk_pct` in $(0, 100]$, and labels are unique.
- [ ] `base_annualized_vol` is the sleeve's **measured volatility at unit sizing**, not a target — and the date it was last refreshed is recorded.
- [ ] Volatilities are fractions (`0.15`), percentages are on a 0–100 scale (`15.0`); the two are not mixed.
- [ ] `holding_period_days` reflects the sleeve's actual holding period, and `trading_days_per_year` matches the market traded.

## Risk budget
- [ ] Horizon budgets sum to $\le 100\%$, and the exact total (not a rounded one) has been read from the report.
- [ ] Each horizon's volatility target is **derived from its budget** ($\sigma_h^{\text{target}} = b_h\sigma_p$), not declared alongside it.
- [ ] The position size scalar falls as sleeve volatility rises — a scalar rising with volatility has the sign backwards.
- [ ] The invariant $\sum_h k_h\sigma_h^{\text{base}} = \sigma_p$ holds for a 100%-allocated book.
- [ ] `under_allocated` has been reviewed: unused risk capacity is a decision, not an error, but it should be a deliberate one.

## Drawdown budget
- [ ] A `portfolio_max_drawdown_limit_pct` is configured — otherwise `is_within_limits` is `None`, meaning *not evaluated*, not *passed*.
- [ ] The sum of per-horizon drawdown limits fits inside the portfolio limit, on the assumption that horizons draw down together.
- [ ] No horizon's drawdown limit alone exceeds the portfolio limit (`is_within_limits` is `True` for every sleeve).
- [ ] `drawdown_limit_below_one_sigma` is `False`, or the sleeve's limit is knowingly set inside routine holding-period noise.

## Scope and enforcement
- [ ] The caller **gates on** `over_allocated` and `drawdown_over_allocated` — this module reports, it does not block.
- [ ] An independent drawdown/kill-switch control exists; this skill checks limit *consistency*, it does not detect or stop a drawdown.
- [ ] It is understood that the allocation is comonotonic (no diversification credit) and will be conservative relative to a covariance-aware engine — the budgets have not been inflated to compensate.
- [ ] Correlation-aware budgeting is handled elsewhere if a covariance matrix is available.
- [ ] Defaults (15% portfolio vol target, 252 trading days, the $3.0$ volatility sanity bound) have been calibrated and the rationale recorded — they are library defaults, not industry standards.

## Testing
- [ ] Automated Testing: Run `python -m unittest discover -s skills/risk-budget-allocation-across-time-horizons/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
