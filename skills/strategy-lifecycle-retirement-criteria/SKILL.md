---
name: strategy-lifecycle-retirement-criteria
description: >-
  Production-grade Strategy Lifecycle Retirement Engine evaluating quantitative alpha decay, Information Ratio thresholds, Information Coefficient t-statistics, live vs backtest performance drift, and automated decommissioning decision matrices.
domain: Investment Governance & Capital Allocation
subdomain: Strategy Lifecycle Governance
tags: ["strategy-lifecycle", "strategy-retirement", "alpha-decay", "information-ratio", "ic-t-stat", "performance-drift"]
brokers_frameworks: ["Quantitative Strategy Governance", "Alpha Decay Monitoring", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing the active-to-retired transition of quantitative trading strategies across an institutional multi-strategy fund. Over time, quantitative strategies suffer from alpha decay due to market structural shifts, crowded trades, or arbitraged inefficiencies. To prevent "falling in love" with decaying strategies, this engine evaluates 4 quantitative guardrails (Information Ratio $\ge 0.50$, Max Live Drawdown $\le 1.5\times$ Backtest DD, IC t-stat $\ge 1.96$, Performance Drift $\ge -40\%$) to issue automated lifecycle decisions (`ACTIVE_HEALTHY`, `NEEDS_REVIEW`, `REDUCE_ALLOCATION`, `MANDATORY_RETIREMENT`).

## Prerequisites

- Strategy performance metrics payload (`StrategyPerformanceMetrics`: `strategy_id`, `backtest_sharpe`, `backtest_max_drawdown_pct`, `live_sharpe`, `live_max_drawdown_pct`, `live_information_ratio`, `live_ic_t_stat`, `live_realized_annual_return_pct`, `backtest_annual_return_pct`).

## Workflow

1. **Information Ratio & IC Audit**:
   - Check if $\text{IR}_{\text{live}} < 0.50$ (alpha decay breach).
   - Check if IC t-stat $< 1.96$ ($95\%$ confidence decay breach).
2. **Drawdown & Performance Drift Audit**:
   - Check if $\text{DD}_{\text{live}} > 1.5 \times \text{DD}_{\text{backtest}}$ (drawdown breach).
   - Check if $\text{Drift}_{\text{return}} < -40\%$ (live vs backtest performance drift breach).
3. **Multi-Criteria Decision Classification**:
   - 0 Breaches $\implies$ `ACTIVE_HEALTHY`.
   - 1 Breach $\implies$ `NEEDS_REVIEW` (Watchlist).
   - 2 Breaches $\implies$ `REDUCE_ALLOCATION` (Cut capital 50%).
   - $\ge 3$ Breaches $\implies$ `MANDATORY_RETIREMENT` (Decommission immediately).
4. **Execution Output**: Output structured `StrategyRetirementReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Alpha Decay**: Continuing to trade a strategy whose Information Coefficient t-stat has decayed below statistical significance ($t < 1.96$), resulting in negative alpha.
- **Overfitting Backtests without Live Thresholds**: Setting unrealistic backtest expectations and failing to enforce quantitative drift limits ($\ge -40\%$).
- **Emotional Parameter Tweaking**: Repeatedly adjusting parameters on a decaying strategy instead of executing a disciplined retirement procedure.

## Verification

- Instantiate `StrategyLifecycleRetirementEngine`. Pass healthy metrics ($\text{IR}=1.2$, $\text{t-stat}=2.5$, $\text{DD}=11\%$) $\implies$ verify `decision = ACTIVE_HEALTHY` and `is_retired = False`. Pass decaying metrics ($\text{IR}=-0.2$, $\text{t-stat}=0.4$, $\text{DD}=20\%$) $\implies$ verify `decision = MANDATORY_RETIREMENT` with 4 breached criteria.
- Run `python scripts/test_strategy_lifecycle_retirement_criteria.py`.

## Related Skills

- `strategy-performance-decay-detection-vs-market-wide-decay`
- `strategy-decommissioning-and-position-unwind-procedure`
---
