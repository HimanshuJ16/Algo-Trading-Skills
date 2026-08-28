# Workflows — risk-budget-allocation-across-time-horizons

## 1. Define the horizon sleeves

- One `TimeHorizonBucket` per horizon, with a **unique** `horizon_label`. Duplicates are
  summed into the total twice while appearing once in the report, so they are rejected.
- `allocated_risk_pct` ($b_h$, in percent) is the sleeve's share of the portfolio risk
  budget, in $(0, 100]$.
- `base_annualized_vol` ($\sigma_h^{\text{base}}$, a fraction) is **what the sleeve
  already does at unit sizing**, not what you want it to do. Source it from a realized
  volatility estimate — `dynamic-position-sizing-based-on-realized-volatility` — or from
  the sleeve's backtested volatility, and record when it was last refreshed.
- `holding_period_days` is the sleeve's typical holding period in trading days, $\ge 1$.
- `max_drawdown_limit_pct` is the sleeve's own drawdown limit as a percentage of
  portfolio equity, in $(0, 100]$.

## 2. Configure the engine

```python
engine = RiskBudgetAllocationEngine(
    total_portfolio_vol_target=0.15,        # the budget being divided
    portfolio_max_drawdown_limit_pct=20.0,  # omit to skip the drawdown check entirely
    trading_days_per_year=252,
)
```

Invalid configuration raises rather than defaulting: a zero or negative volatility target
would otherwise size every horizon from nothing, and a percent-shaped target (`15`
instead of `0.15`) would scale every position by $100\times$.

## 3. Derive the budget-implied volatility target

$$\sigma_h^{\text{target}} = b_h \times \sigma_p$$

This is the risk budgeting constraint (Bruder & Roncalli 2012, Sec. 2.1) with volatility
as the risk measure, expressed in volatility units rather than dollars. The budget is
authoritative; the volatility target follows from it. Declaring both independently is the
defect this design exists to prevent.

## 4. Compute the position size scalar

$$k_h = \frac{\sigma_h^{\text{target}}}{\sigma_h^{\text{base}}}$$

The scalar is **inverse** to the sleeve's own volatility. Worked example at
$\sigma_p = 15\%$:

| Horizon | $b_h$ | $\sigma_h^{\text{target}}$ | $\sigma_h^{\text{base}}$ | $k_h$ |
|---|---|---|---|---|
| INTRADAY | 15% | 2.25% | 35% | 0.0643 |
| SHORT_TERM | 25% | 3.75% | 22% | 0.1705 |
| MEDIUM_TERM | 35% | 5.25% | 18% | 0.2917 |
| LONG_TERM | 25% | 3.75% | 12% | 0.3125 |

The volatile intraday sleeve gets the smallest multiplier: it reaches its budget with the
least exposure. A scalar that *rises* with sleeve volatility has the sign backwards and
will oversize the riskiest sleeve.

Check the invariant: $\sum_h k_h \sigma_h^{\text{base}} = 0.15 = \sigma_p$ exactly.

## 5. Validate the total risk budget

- Sum with `math.fsum` so the verdict does not depend on bucket order, and compare against
  $100\% \pm$ `ALLOCATION_TOLERANCE_PCT`.
- `over_allocated` is a breach. `under_allocated` is not — it means risk capacity is
  going unused, which may be deliberate. `unallocated_risk_pct` quantifies it.
- The reported `total_risk_budget_pct` is the exact sum, never rounded, so the number in
  the audit record can never contradict the flag beside it.

## 6. Audit the drawdown budget

Only when `portfolio_max_drawdown_limit_pct` is configured:

- $\sum_h \text{dd}_h$ vs the portfolio limit, under the comonotonic assumption that
  horizons can draw down together. Four sleeves at 8% each under a 20% portfolio limit is
  a breach even though every individual limit reads green.
- `is_within_limits` per horizon answers only "can this sleeve breach the portfolio limit
  on its own?" It is `None` when the check did not run.
- `drawdown_limit_below_one_sigma` flags a limit set inside one holding-period sigma
  ($\sigma_h^{\text{target}}\sqrt{T_h/F}$) — a limit that fires on ordinary P&L. Treat it
  as an order-of-magnitude sanity check; the square-root-of-time rule understates risk
  under jumps (see `references/standards.md`).

## 7. Consume the report

```python
report = engine.allocate_risk_budget(buckets)
if report.over_allocated or report.drawdown_over_allocated:
    raise RuntimeError(report.audit_notes)   # the engine blocks nothing on its own
scalars = {a.horizon_label: a.position_size_scalar for a in report.horizon_allocations}
```

`allocate_risk_budget` returns flags; it does not stop trading. Gate on the flags before
any scalar reaches a sizing path.

## 8. Re-run when inputs move

The allocation is a configuration-time decision. Re-run it when a sleeve's realized
volatility diverges from its `base_annualized_vol`, when a horizon is added or retired,
or when the portfolio volatility budget changes. Nothing here reacts to intraday regime
shifts — see `regime-detection-for-strategy-switching`.
