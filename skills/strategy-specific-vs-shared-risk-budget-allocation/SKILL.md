---
name: strategy-specific-vs-shared-risk-budget-allocation
description: >-
  Production-grade Strategy-Specific vs Shared Risk Budget Allocation Engine performing Euler risk decomposition, Component VaR calculations, Marginal Contribution to Risk (MCR), and dual-tier risk limit audits.
domain: Risk Management & Portfolio Optimization
subdomain: Euler Risk Budgeting & Allocation
tags: ["risk-budgeting", "euler-allocation", "component-var", "mcr", "strategy-risk-limits", "portfolio-var"]
brokers_frameworks: ["Euler Risk Allocation Framework", "Component VaR Optimization", "Python Dataclasses", "numpy"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when allocating capital and managing risk limits across a multi-strategy quantitative fund. Managing risk solely at the individual strategy level ignores diversification benefits, while managing risk solely at the aggregate portfolio level allows a single volatile strategy to crowd out the fund's risk capacity. This engine computes Marginal Contribution to Risk ($\text{MCR}_i = \frac{(\Sigma w)_i}{\sigma_p}$), Component VaR ($\text{CVaR}_i = w_i \times \text{MCR}_i \times \text{VaR}_{95\%}$), and audits dual-tier limits (Standalone Volatility limits vs Shared Risk Contribution % budgets).

## Prerequisites

- Strategy risk budget specifications (`StrategyRiskBudgetSpec`: `strategy_id`, `target_capital_usd`, `max_standalone_volatility_pct`, `max_shared_risk_contribution_pct`).
- Strategy return covariance matrix ($N \times N$ matrix $\Sigma$).
- Order of strategy IDs matching covariance matrix rows/columns.

## Workflow

1. **Portfolio Volatility & VaR Estimation**:
   - Compute portfolio variance: $\sigma_p^2 = w^T \Sigma w$.
   - Calculate 95% Parametric VaR: $\text{VaR}_{95\%} = \text{Capital} \times \sigma_p \sqrt{252} \times 1.645$.
2. **Euler Marginal Contribution to Risk (MCR)**:
   - Calculate $\text{MCR}_i = \frac{(\Sigma w)_i}{\sigma_p}$.
3. **Component Risk & Euler Identity Audit**:
   - Calculate Component Risk Fraction: $\text{CVaR}_i = \frac{w_i \times \text{MCR}_i}{\sigma_p}$.
   - Verify Euler identity: $\sum \text{CVaR}_i = 1.0$ ($100\%$).
4. **Dual-Tier Limit Audit & Capital Adjustment**:
   - Check Standalone Volatility breaches ($>\text{max\_standalone\_volatility\_pct}$).
   - Check Shared Risk Contribution breaches ($>\text{max\_shared\_risk\_contribution\_pct}$).
   - Calculate capital scaling adjustment factors: $\text{Adjustment} = \min\left(1.0, \frac{\text{Budget}}{\text{Actual Risk}}\right)$.
5. **Execution Output**: Output structured `PortfolioRiskBudgetAllocationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Standalone Volatility with Component Risk**: Reducing allocation to a strategy with high standalone volatility that actually provides strong negative correlation diversification benefits to the portfolio.
- **Euler Decomposition Non-Summation**: Using non-homogeneous risk measures where component risks do not sum to total portfolio risk.
- **Ignoring Risk Parity Drift**: Failing to re-scale capital allocations when a strategy's share of total portfolio VaR exceeds its designated risk budget.

## Verification

- Instantiate `StrategySpecificVsSharedRiskBudgetEngine`. Evaluate 2-strategy portfolio $\implies$ verify `is_euler_decomposition_valid = True` and sum of component risks equals $100.0\%$. Pass high volatility covariance for `STAT_ARB` ($47\%$ standalone vol vs $15\%$ limit) $\implies$ verify `STAT_ARB` flagged in `breached_strategies` with recommended capital scaling factor $< 1.0$.
- Run `python scripts/test_strategy_specific_vs_shared_risk_budget_allocation.py`.

## Related Skills

- `risk-parity-allocation-across-strategies`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
---
