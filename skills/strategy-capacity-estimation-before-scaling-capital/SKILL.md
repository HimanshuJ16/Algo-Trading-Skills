---
name: strategy-capacity-estimation-before-scaling-capital
description: >-
  Production-grade strategy capacity estimation engine incorporating Almgren-Chriss square-root market impact, bid-ask spread friction, ADV turnover limits, and Sharpe ratio decay curve modeling before scaling capital AUM.
domain: Portfolio & Risk Management
subdomain: AUM Capacity Estimation & Capital Scaling
tags: ["strategy-capacity", "aum-scaling", "almgren-chriss", "market-impact", "sharpe-decay", "turnover-limit"]
brokers_frameworks: ["Almgren-Chriss Market Impact Model", "Portfolio Capacity Frameworks", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when evaluating quantitative trading strategy capacity before allocating larger institutional AUM or scaling capital. As AUM scales, trade sizes expand, triggering non-linear market impact slippage (Almgren-Chriss square-root model) and bid-ask spread friction. This market impact erodes net strategy returns and decays the realized Sharpe ratio. This engine models the Sharpe ratio decay curve across AUM steps, identifies optimal PnL capacity, and enforces maximum ADV participation limits (e.g. 5% ADV).

## Prerequisites

- Strategy performance parameters (`StrategyParameters`: `strategy_id`, `annual_gross_return_pct`, `annual_volatility_pct`, `daily_turnover_pct`, `avg_daily_volume_usd`, `avg_daily_volatility_pct`, `half_spread_bps`, `max_participation_rate_pct`, `min_acceptable_sharpe`).

## Workflow

1. **Daily Market Impact & Friction Modeling**:
   - Model Almgren-Chriss square-root impact: $\text{Impact} = \gamma \cdot \sigma_{\text{daily}} \cdot \sqrt{\frac{\text{Trade Size}}{\text{ADV}}}$.
   - Add bid-ask spread cost: $\text{Spread Cost} = \text{Turnover} \times \text{Spread}$.
2. **AUM Scaling Simulation & Decay Curve Construction**:
   - Simulate net returns across AUM steps ($1\text{M} \dots 100\text{M}$).
   - Calculate Net Sharpe ratio: $\text{Sharpe}_{\text{net}} = \frac{R_{\text{gross}} - \text{Costs}}{\sigma_{\text{strategy}}}$.
3. **Capacity Limit & Limiting Factor Determination**:
   - Identify maximum AUM where $\text{Sharpe}_{\text{net}} \ge \text{min\_acceptable\_sharpe}$ and $\text{Participation} \le \text{max\_participation\_rate}$.
   - Identify limiting factor (`ADV_PARTICIPATION_LIMIT` vs `MIN_SHARPE_BREACH`).
4. **Execution Output**: Output structured `StrategyCapacityReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Non-Linear Market Impact**: Assuming execution costs scale linearly with AUM, drastically overestimating capacity for high-turnover strategies.
- **Exceeding Exchange ADV Limits**: Sizing orders above 5% - 10% of ADV, incurring severe market impact and signalling intentions to predatory high-frequency algorithms.
- **Relying Solely on Frictionless Backtest Sharpe**: Projecting small-scale paper trading performance directly onto institutional AUM without modeling Sharpe decay.

## Verification

- Instantiate `StrategyCapacityEstimatorEngine`. Simulate $25\%$ gross return strategy ($10\%$ daily turnover, $50\text{M}$ ADV) $\implies$ verify frictionless Sharpe $1.67$. Scale AUM from $1\text{M}$ to $100\text{M}$ $\implies$ verify Net Sharpe decays as AUM increases and max capacity AUM identified.
- Run `python scripts/test_strategy_capacity_estimation_before_scaling_capital.py`.

## Related Skills

- `portfolio-construction-with-transaction-cost-awareness`
- `incremental-capital-deployment-for-new-strategies`
---
