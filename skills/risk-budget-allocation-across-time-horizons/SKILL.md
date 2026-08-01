---
name: risk-budget-allocation-across-time-horizons
description: >-
  Risk budget allocation engine distributing total portfolio risk across multiple time horizon buckets (intraday, short-term, medium-term, long-term) with per-horizon volatility targets, position size scalars, and over-allocation detection.
domain: Portfolio & Risk Management
subdomain: Risk Budget Governance & Multi-Horizon Allocation
tags: ["risk-budget", "time-horizons", "volatility-targeting", "position-sizing", "portfolio-risk", "multi-horizon"]
brokers_frameworks: ["Risk Budgeting (Roncalli)", "Volatility Targeting", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when running a multi-strategy portfolio that spans different trading horizons — from intraday scalping to multi-week momentum — and needing to allocate total portfolio risk budget across time horizons. Without explicit risk budgeting, correlated drawdowns across horizons can exceed total portfolio risk tolerance. This engine distributes risk budgets, sets per-horizon volatility targets, computes position size scalars, and detects over-allocation breaches.

## Prerequisites

- Time horizon bucket definitions (`horizon_label`, `holding_period_days`, `allocated_risk_pct`, `annualized_vol_target`, `max_drawdown_limit_pct`).
- Total portfolio annualized volatility target (default 15%).

## Workflow

1. **Horizon Bucket Registration**:
   - Define risk allocation percentage and volatility target per time horizon.
2. **Position Size Scalar Computation**:
   - Calculate $\text{Scalar} = \frac{\text{Horizon Vol Target}}{\text{Portfolio Vol Target}}$.
3. **Over-Allocation Validation**:
   - Verify total allocated risk $\le 100\%$.
4. **Budget Report Generation**: Output structured `RiskBudgetAllocationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Uncorrelated Assumption Across Horizons**: Assuming intraday and weekly strategies are uncorrelated, then allocating 100% risk to each.
- **No Per-Horizon Drawdown Limit**: Allowing one horizon to consume the entire portfolio risk budget during a drawdown.
- **Static Allocation Without Regime Awareness**: Not adjusting horizon risk budgets when volatility regimes shift.

## Verification

- Instantiate `RiskBudgetAllocationEngine`. Allocate 4 horizons totaling 100% $\implies$ verify `RISK_BUDGET_VALID`. Allocate 50% + 60% = 110% $\implies$ verify `RISK_BUDGET_OVER_ALLOCATED`. Verify position size scalar for intraday (10% vol target / 15% portfolio target ≈ 0.667).
- Run `python scripts/test_horizon_risk_allocator.py`.

## Related Skills

- `risk-adjusted-performance-attribution-per-strategy`
- `regime-detection-for-strategy-switching`
---
