# Pre-Flight / Sign-off Checklist — rebalancing-frequency-optimization-cost-vs-drift

## Input snapshot
- [ ] All weights, values, and cost inputs are finite — non-finite values are rejected **before** any threshold comparison.
- [ ] Target weights sum to $1$; current weights sum to $1$.
- [ ] Each `current_weight` agrees with `asset_value_usd / total_value` within `weight_tolerance`.
- [ ] No duplicate symbols; total portfolio value is finite and $> 0$ (no fallback substitution).

## Calibration
- [ ] `drift_penalty_lambda` has been calibrated — it is a placeholder default, not a recommendation.
- [ ] `drift_horizon_periods` matches the interval between evaluations, in the same time unit as $\lambda$ (e.g. $1/252$ for a daily job with an annual $\lambda$).
- [ ] It is understood that shortening the evaluation interval must not, by itself, make rebalancing look more attractive.
- [ ] `max_drift_threshold_pct` reflects the mandate's tracking-error budget, and the $\ge$ (inclusive) band edge is accepted.
- [ ] `min_trade_threshold_pct` is non-zero — a quadratic penalty against linear costs is positive for arbitrarily small drifts.

## Destination and trade set
- [ ] Destination policy chosen deliberately: `None` (full rebalance to target) or a boundary $b <$ `max_drift_threshold_pct`.
- [ ] Uniform shrink verified to leave residual drifts summing to zero and post-trade weights summing to one.
- [ ] Transaction cost is priced on the **traded** weight after shrink and per-leg filters, not on raw drift.
- [ ] `suppressed_legs` is reviewed each run; the resulting cash imbalance is settled explicitly.
- [ ] `REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES` is wired to an alert — when it follows a band breach it is an unremediated mandate breach, not a flat book.
- [ ] Cash is reserved for fees and slippage — buy and sell notionals net to zero and do not fund them.

## Scope
- [ ] $\sum_i d_i^2$ is not tracking error; correlation is handled elsewhere if it matters.
- [ ] Tax-lot impact assessed separately for taxable accounts.
- [ ] Fixed/tiered/non-linear costs assessed separately if they dominate.
- [ ] Order slicing, venue routing, and order lifecycle handled downstream.

## Testing
- [ ] Run `python -m unittest discover -s skills/rebalancing-frequency-optimization-cost-vs-drift/scripts` — 100% pass rate.
- [ ] Negative checks confirmed to raise: `NaN` weight, infinite value, zero portfolio value, weight sums off $1$, value/weight disagreement, duplicate symbol, negative fee, destination $\ge$ threshold.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
