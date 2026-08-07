---
name: portfolio-construction-with-transaction-cost-awareness
description: >-
  Transaction-cost aware portfolio construction engine incorporating proportional commissions, bid-ask spreads, quadratic market impact, and no-trade buffer bands.
domain: Portfolio Multi Strategy
subdomain: Net Utility Portfolio Optimization & Turnover Management
tags: ["portfolio-construction", "transaction-costs", "rebalancing", "no-trade-band", "turnover-control", "market-impact", "mean-variance"]
brokers_frameworks: ["CVXPY / Quadratic Programming", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when rebalancing multi-asset or multi-strategy portfolios where trading costs (bid-ask spread, broker commissions, and market impact penalties) erode gross signal returns. Traditional Mean-Variance Optimization (MVO) assumes zero-friction rebalancing, resulting in excessive micro-rebalancing turnover that degrades net Sharpe ratios. This engine incorporates proportional transaction costs, quadratic market impact penalties, and No-Trade Buffer Bands ($|\Delta w| > \text{threshold}$) to optimize net portfolio utility.

## Prerequisites

- Asset alpha specs (`symbol`, `expected_return`, `current_weight`, `target_weight`).
- Transaction cost specifications (`commission_rate`, `spread_cost_bps`, `impact_coeff`).
- Optimizer config (`rebalance_threshold`: float = 0.02, `max_turnover_limit`: float = 0.50).

## Workflow

1. **No-Trade Buffer Band Filtering**:
   - Compute proposed weight change: $\Delta w_i = w_{\text{target}, i} - w_{\text{current}, i}$.
   - If $|\Delta w_i| \le \text{threshold}$, suppress trade ($w_{\text{final}, i} = w_{\text{current}, i}$).
   - Else set $w_{\text{final}, i} = w_{\text{target}, i}$.
2. **Transaction Cost & Market Impact Calculation**:
   - Compute total turnover: $\text{Turnover} = \sum |\Delta w_{\text{traded}, i}|$.
   - Compute transaction cost for traded assets:
     $$\text{TC}_i = (c_{\text{commission}} + c_{\text{spread}}) \cdot |\Delta w_i| + c_{\text{impact}} \cdot (\Delta w_i)^2$$
3. **Net Expected Utility Evaluation**:
   - Compute Net Return $= \sum (w_{\text{final}, i} \cdot \mu_i) - \text{TotalTC}$.
4. **Audit Report Generation**: Output structured `TCAwarePortfolioReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Chasing Micro Signals**: Rebalancing asset weights for $0.1\%$ weight shifts, incurring $0.05\%$ transaction costs that consume all alpha.
- **Ignoring Quadratic Market Impact**: Assuming linear execution costs for large block rebalances, underestimating market impact.
- **Excessive Rebalancing Frequency**: Rebalancing on high-frequency noise instead of using buffer bands or threshold triggers.

## Verification

- Instantiate `PortfolioConstructionEngine`. Input 2 assets (`AAPL` current $40\%$ vs target $41\%$, `MSFT` current $30\%$ vs target $40\%$) with buffer threshold $2\%$. Verify `AAPL` trade is suppressed (inside no-trade band), while `MSFT` trades $10\%$ weight shift. Verify total transaction costs and net expected return calculations.
- Run `python scripts/test_portfolio_construction_with_transaction_cost_awareness.py`.

## Related Skills

- `rebalancing-frequency-optimization-cost-vs-drift`
- `execution-cost-model-recalibration-cadence`
---
