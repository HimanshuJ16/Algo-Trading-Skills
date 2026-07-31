---
name: multi-strategy-reporting-consolidation-for-stakeholders
description: >-
  Multi-strategy reporting consolidation engine aggregating sub-strategy PnL, computing portfolio-level Sharpe ratios, diversification ratios, and stakeholder attribution.
domain: Portfolio Multi Strategy
subdomain: Executive Reporting & Multi-Strategy Performance Consolidation
tags: ["multi-strategy", "reporting", "stakeholder-reporting", "portfolio-attribution", "sharpe-ratio", "diversification-ratio", "pnl-consolidation"]
brokers_frameworks: ["Executive Reporting Engine", "Portfolio Performance Attribution", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when reporting consolidated performance metrics across multiple sub-strategies (e.g. Statistical Arbitrage, Trend Following, Options Market Making) for fund managers, risk committees, and LP investors. Simply summing strategy-level metrics introduces distortions because sub-strategy returns are not perfectly correlated ($\rho_{ij} < 1$). This engine synthesizes daily joint portfolio return series, computes portfolio annualized Sharpe ratios, calculates diversification benefit ratios ($\frac{\sum w_k \sigma_k}{\sigma_p} > 1.0$), and produces executive stakeholder reports.

## Prerequisites

- Sub-strategy telemetry payloads (`strategy_id`, `allocated_capital_usd`, `realized_pnl_usd`, `unrealized_pnl_usd`, `daily_returns`, `max_drawdown_pct`).
- Reporting config (`portfolio_name`, `risk_free_rate_ann`: e.g. 0.04, `trading_days_per_year`: e.g. 252).

## Workflow

1. **Capital & PnL Aggregation**:
   - Compute total allocated capital: $C_{\text{total}} = \sum C_k$.
   - Compute total net PnL: $\text{PnL}_{\text{total}} = \sum (\text{realized}_k + \text{unrealized}_k)$.
2. **Joint Return Synthesis & Volatility Calculation**:
   - Synthesize portfolio weighted daily return series:
     $$R_{p, t} = \sum_{k=1}^K w_k R_{k, t} \quad \text{where } w_k = \frac{C_k}{C_{\text{total}}}$$
   - Compute portfolio annualized volatility $\sigma_p = \text{std}(R_{p, t}) \cdot \sqrt{252}$.
3. **Sharpe Ratio & Diversification Benefit Audit**:
   - Compute portfolio Sharpe ratio: $SR_p = \frac{\text{mean}(R_{p, t}) \cdot 252 - R_f}{\sigma_p}$.
   - Compute Diversification Ratio:
     $$\text{Diversification Ratio} = \frac{\sum w_k \sigma_k}{\sigma_p}$$
4. **Audit Report Generation**: Output structured `ConsolidatedStakeholderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naive Metric Summation**: Averaging sub-strategy Sharpe ratios instead of calculating the true portfolio-level Sharpe ratio from the joint return series.
- **Ignoring Return Correlation**: Failing to account for diversification benefits when evaluating multi-strategy portfolio risk.
- **Unweighted Return Aggregation**: Treating equal dollar allocations across strategies with vastly different capital bases.

## Verification

- Instantiate `MultiStrategyReportingConsolidatorEngine`. Consolidate 2 un-correlated strategies (Strategy A: $500k allocated, Strategy B: $500k allocated) $\implies$ verify total capital = $\$1,000,000$, portfolio volatility is lower than individual volatilities, diversification ratio $> 1.2$, and status `REPORT_CONSOLIDATED_SUCCESS`.
- Run `python scripts/test_multi_strategy_reporting_consolidation_for_stakeholders.py`.

## Related Skills

- `strategy-performance-attribution-vs-market-beta`
- `benchmark-portfolio-for-multi-strategy-performance-context`
---
