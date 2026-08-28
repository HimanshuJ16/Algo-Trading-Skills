---
name: risk-budget-allocation-across-time-horizons
description: Use when a multi-horizon book (intraday through multi-week) needs one portfolio
  volatility budget split across horizon sleeves, deriving each sleeve's volatility target
  and position-size scalar from its risk budget and auditing the 100% risk cap and the
  portfolio drawdown cap.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- risk-budgeting
- time-horizons
- volatility-targeting
- position-sizing
- drawdown-limits
brokers_frameworks:
- Risk Budgeting (Bruder & Roncalli 2012)
- Volatility Targeting
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when one account runs strategies at genuinely different holding periods — intraday scalping alongside multi-week momentum — and the question is how much of the *portfolio's* risk each horizon may consume. A risk budget is an amount of risk, and the budgeting constraint is that a sleeve's risk contribution equals its budget (Bruder & Roncalli 2012, Sec. 2.1). Denominating that budget in annualized volatility, this engine turns each horizon's budget share into a volatility target and a position-size scalar, then audits the total against the 100% risk cap and, optionally, the sum of per-horizon drawdown limits against a portfolio drawdown cap.

## When NOT to Use

- **When you have a covariance matrix between the sleeves.** This module has only standalone volatilities, so it cannot compute a true Euler risk contribution ($RC_h = x_h \cdot \partial R/\partial x_h$). It applies the *comonotonic* convention instead — see the Workflow. With correlations in hand, use `strategy-specific-vs-shared-risk-budget-allocation` (Euler / Component VaR) or `risk-parity-allocation-across-strategies` (ERC), both of which allocate on the real covariance and will produce larger, diversification-aware sizes.
- **As a drawdown control.** The engine checks whether the *stated* per-horizon drawdown limits are mutually consistent with a portfolio limit. It does not measure equity, does not know a drawdown is happening, and stops nothing. Pair it with `kill-switch-and-drawdown-circuit-breakers`.
- **As a capital allocator.** The budget here is denominated in volatility, not in notional or margin. Equal risk is not equal capital. For capital caps and buying-power ceilings use `multi-strategy-capital-allocation-limits`.
- **When `base_annualized_vol` is a guess.** Every output is a linear function of that input. A sleeve whose realized volatility is double the supplied estimate runs at double its budget, and this module cannot detect that — it never sees returns. Estimate it with `dynamic-position-sizing-based-on-realized-volatility` and refresh it.
- **As a leverage control.** The scalar converts volatility budgets into position multipliers and is unbounded above; it says nothing about the notional or margin that multiplier implies. Bound leverage with `leverage-limit-enforcement-across-instruments`.
- **As an intraday risk control.** The allocation is a configuration-time decision. Nothing here reacts to a regime shift within the session; see `regime-detection-for-strategy-switching`.

## Prerequisites

- Horizon sleeve definitions (`TimeHorizonBucket`: `horizon_label`, `holding_period_days`, `allocated_risk_pct`, `base_annualized_vol`, `max_drawdown_limit_pct`). Labels must be unique — a duplicate is summed into the total twice while appearing once in the report.
- **`base_annualized_vol` is an estimate of what the sleeve already does at unit (scalar = 1.0) sizing, not a target.** The target is derived from the budget. Supplying a target here inverts the meaning of every output.
- Total portfolio annualized volatility budget $\sigma_p$ (default $0.15 = 15\%$).
- Units: `*_pct` fields are percentages on a 0–100 scale; volatilities are fractions. Passing `15` where `0.15` is meant is rejected, not silently scaled.
- Optional: a portfolio-level `portfolio_max_drawdown_limit_pct`. Omit it and the drawdown check does not run — `is_within_limits` is then `None`, meaning *not evaluated*, not *passed*.
- `trading_days_per_year` matching the market actually traded (252 default) if the holding-period diagnostic is to mean anything.

## Workflow

1. **Register horizon sleeves**:
   - Assign each horizon a share $b_h$ of the portfolio risk budget, its own ex-ante volatility at unit sizing, and its own drawdown limit.
   - **Decision point — the budget and the volatility target are not independent knobs.** Declaring both invites a book where the budgets say 20/50 and the sizing delivers something else entirely. The budget is authoritative; the vol target is derived from it.

2. **Derive the budget-implied volatility target**:
   $$\sigma_h^{\text{target}} = b_h \times \sigma_p$$
   This is the risk budgeting constraint of Bruder & Roncalli (2012) with volatility as the risk measure, expressed in volatility units rather than dollars.

3. **Compute the position size scalar**:
   $$k_h = \frac{\sigma_h^{\text{target}}}{\sigma_h^{\text{base}}}$$
   - **Decision point — the scalar is *inverse* to the sleeve's own volatility.** A sleeve running at 35% annualized gets a *smaller* multiplier than one running at 12% for the same budget, because it consumes its budget with less exposure. A scalar that rises with sleeve volatility is the wrong sign and will systematically oversize the riskiest sleeve.

4. **Validate the total against the 100% cap**:
   - Sum with `math.fsum`, compare against $100\% \pm$ `ALLOCATION_TOLERANCE_PCT`, and report the exact total. **Under-allocation is reported separately and is not a breach** — it means portfolio risk capacity is going unused, which is a decision, not an error.
   - **Decision point — the report is advisory.** `allocate_risk_budget` returns flags; it blocks nothing. The caller must gate on `over_allocated` and `drawdown_over_allocated` before sizing anything.

5. **Audit the drawdown budget** (when a portfolio limit is configured):
   - Compare $\sum_h \text{dd}_h$ against the portfolio limit under the comonotonic assumption that horizons can draw down together, and flag any single horizon whose limit alone exceeds the portfolio limit.
   - The holding-period diagnostic $\sigma_h^{\text{target}}\sqrt{T_h / F}$ flags a drawdown limit that sits inside one holding-period sigma — a limit that will fire on ordinary P&L rather than on a risk event.

6. **Report**: emit `RiskBudgetAllocationReport`.

**The conservatism this buys, stated plainly.** Volatility is sub-additive: $\sigma(\sum_h X_h) \le \sum_h \sigma(X_h)$, with equality only under perfect correlation. Sizing every sleeve to $b_h\sigma_p$ makes the *sum* of sleeve volatilities equal $\sigma_p$ exactly, so realized portfolio volatility is at most $\sigma_p$. That is a ceiling, not a forecast, and it credits no diversification between horizons. A correlation-aware engine will permit larger positions for the same true risk.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A risk budget that does not reach the sizing.** If the position-size scalar is computed from vol targets alone, two horizons with 5% and 50% budgets get identical scalars and the budget is decorative — validated, reported, and ignored by the only output that moves money. The scalar must be a function of $b_h$.
- **Assuming the horizons diversify.** Intraday and weekly sleeves are not independent; correlations rise precisely when it matters. The comonotonic convention here assumes they move together, which is why it is safe to use without a covariance matrix — and why it will look "too small" in calm markets. Do not compensate by inflating the budgets.
- **A NaN allocation passing the cap.** `float('nan') > 100.0` is `False`, so a naive total silently returns *valid* for a budget that is not a number. Non-finite inputs are rejected at the door, never summed.
- **A negative allocation offsetting a positive one.** 150% plus −60% totals 90% and passes a naive cap. A sign error in one horizon then licenses a 150% allocation in another.
- **A running float total fabricating a breach.** Two-decimal percentages intended to total exactly 100% can accumulate to 100.00000000000001. Rounding the reported total to 2 dp afterwards produces an audit record reading "100.0%, over-allocated" — a self-contradicting risk artifact. Sum with `fsum`, compare with a tolerance, and report the exact figure.
- **Reading `is_within_limits` as an all-clear.** It answers one narrow question — can this horizon breach the portfolio drawdown limit on its own — and is `None` when no portfolio limit was configured. It is not a verdict on the portfolio total.
- **Drawdown limits that sum past the portfolio limit.** Four sleeves at 8% each under a 20% portfolio limit means any three of them drawing down together exhausts the firm's tolerance while every individual limit still reads green.
- **A drawdown limit inside one holding-period sigma.** A 2% limit on a sleeve whose budgeted volatility over its holding period is 3% will be hit by routine noise, and the sleeve will spend its life halted for reasons unrelated to risk.
- **Treating the scalar as bounded.** $k_h$ is unbounded above: a sleeve with a very low base volatility needs an enormous multiplier to reach its budget. A 20% budget on a $\sigma_p=15\%$ portfolio against a sleeve realizing 0.0001% annualized yields a scalar of 30,000 — arithmetically correct ($k_h\sigma_h^{	ext{base}}$ is still exactly 3%) and operationally unusable, because the notional required is a leverage question this module never sees. Cap leverage separately with `leverage-limit-enforcement-across-instruments`.
- **Stale `base_annualized_vol`.** Sized from a 12% estimate, a sleeve now realizing 24% runs at double its budget with every report still showing green.

## Verification

- Instantiate `RiskBudgetAllocationEngine(total_portfolio_vol_target=0.15)`. Allocate `INTRADAY` 20% at `base_annualized_vol=0.10` $\implies$ `budget_implied_vol_target` $= 0.20 \times 0.15 = 0.03$ and `position_size_scalar` $= 0.03/0.10 = 0.30$. Allocate `LONG_TERM` 20% at $0.18$ $\implies$ scalar $= 1/6$: the same budget, the more volatile sleeve, the *smaller* scalar.
- Invariant: for any book totalling 100%, $\sum_h k_h \sigma_h^{\text{base}} = \sigma_p$ exactly.
- Allocate 50% + 60% $\implies$ `RISK_BUDGET_OVER_ALLOCATED`. Allocate 20% + 30% $\implies$ `RISK_BUDGET_VALID` with `under_allocated` true and `unallocated_risk_pct` $= 50$.
- Allocate `23.35 / 65.80 / 9.18 / 1.67` (a running float total exceeds 100) $\implies$ **not** over-allocated.
- With `portfolio_max_drawdown_limit_pct=10.0`, two horizons at 6% each $\implies$ `DRAWDOWN_BUDGET_OVER_ALLOCATED` while both `is_within_limits` are true.
- Negative checks — each must raise: a `NaN` or infinite `allocated_risk_pct`; a negative or zero allocation; `base_annualized_vol` of `0.0`, `-0.10`, or `15.0` (percent/fraction mix-up); a duplicate `horizon_label`; an empty bucket list; `holding_period_days` of `0` or `True`; `total_portfolio_vol_target` of `0.0` or `NaN`.
- Run `python scripts/test_horizon_risk_allocator.py` and confirm 100% pass rate.

## Related Skills

- `strategy-specific-vs-shared-risk-budget-allocation`
- `risk-parity-allocation-across-strategies`
- `dynamic-position-sizing-based-on-realized-volatility`
- `multi-strategy-capital-allocation-limits`
- `kill-switch-and-drawdown-circuit-breakers`
- `correlation-aware-exposure-limits`
- `regime-detection-for-strategy-switching`
- `risk-adjusted-performance-attribution-per-strategy`
