---
name: risk-adjusted-performance-attribution-per-strategy
description: >-
  Risk-adjusted performance attribution engine computing Sharpe, Sortino, Calmar ratios, max drawdown, and marginal risk contribution per strategy within a multi-strategy portfolio.
domain: Portfolio & Risk Management
subdomain: Performance Attribution & Risk Decomposition
tags: ["performance-attribution", "sharpe-ratio", "sortino-ratio", "calmar-ratio", "max-drawdown", "risk-contribution"]
brokers_frameworks: ["Sharpe Ratio (William Sharpe)", "Sortino Ratio", "Calmar Ratio", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing a multi-strategy trading portfolio and needing to decompose performance and risk across individual strategy components. Understanding which strategies deliver the most risk-adjusted return (Sharpe, Sortino, Calmar) and which contribute the most portfolio risk enables informed capital allocation, strategy retirement, and risk budget rebalancing decisions. This engine computes per-strategy risk metrics and marginal risk contribution to the aggregate portfolio.

## Prerequisites

- Per-strategy daily return series (`strategy_id`, `daily_returns`).
- Portfolio weights (optional; defaults to equal-weight).
- Risk-free rate assumption (`risk_free_rate_annual`: default 5%).

## Workflow

1. **Per-Strategy Metric Calculation**:
   - Compute annualized return, annualized volatility, Sharpe ratio, Sortino ratio, max drawdown, and Calmar ratio for each strategy.
2. **Portfolio-Level Blended Returns**:
   - Construct weighted portfolio daily returns from strategy components.
3. **Marginal Risk Contribution**:
   - Calculate each strategy's weighted volatility contribution as a percentage of total.
4. **Attribution Report**: Output structured `PortfolioAttributionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Raw Returns Without Risk Adjustment**: Ranking strategies by total PnL instead of Sharpe/Sortino leads to overweighting volatile strategies.
- **Ignoring Tail Risk**: Sharpe ratio does not capture downside-specific risk; always evaluate Sortino and Calmar alongside.
- **Static Risk-Free Rate**: Using outdated risk-free rate assumptions in rising/falling rate environments skews all ratios.

## Verification

- Instantiate `RiskAdjustedPerformanceAttributionEngine`. Feed positive-return strategy with variance $\implies$ verify Sharpe $> 1.0$ and Sortino $> 0$. Feed high-vol and low-vol strategies $\implies$ verify high-vol contributes more portfolio risk. Feed strategy with drawdown period $\implies$ verify `max_drawdown > 10\%`.
- Run `python scripts/test_risk_adjusted_attribution.py`.

## Related Skills

- `regime-detection-for-strategy-switching`
- `research-idea-pipeline-tracking-and-prioritization`
---
