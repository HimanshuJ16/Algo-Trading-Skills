---
name: backtest-reporting-standardized-tearsheet
description: Use when evaluating completed backtests to generate a standardized performance
  tearsheet computing Sharpe, Sortino, Calmar, max drawdown, win rate, and profit
  factor for objective strategy comparison.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- tearsheet
- performance-metrics
- sharpe-ratio
- drawdown
- backtest-reporting
brokers_frameworks:
- Standardized Tearsheet Generator
- Python NumPy
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill at the conclusion of every backtest run. Reporting fragmented or customized performance metrics makes comparing different trading strategies subject to cherry-picking bias. Generating a standardized performance tearsheet ensures every strategy is benchmarked against identical risk-adjusted returns, downside risk, trade statistics, and drawdown distribution rules.

## Prerequisites

- Daily portfolio returns series $R_t$ or timestamped trade execution log.
- Annual risk-free rate $r_f$ (e.g. 0.04).

## Workflow

1. **Calculate Risk-Adjusted Ratios**: Compute Sharpe ratio, Sortino ratio (downside risk), and Calmar ratio.
2. **Compute Drawdown Statistics**: Calculate maximum drawdown %, average drawdown duration, and recovery time.
3. **Compute Trade Execution Metrics**: Calculate win rate %, profit factor (gross win / gross loss), and average win/loss ratio.
4. **Format Tearsheet Summary**: Generate structured report dictionary for standardized presentation.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Annualizing Sharpe with Wrong Frequency**: Multiplying daily Sharpe by $\sqrt{365}$ instead of trading days $\sqrt{252}$.
- **Ignoring Downside Volatility**: Reporting high Sharpe for strategies with severe left-tail crash risk (negative skewness).

## Verification

- Submit test return series, verify tearsheet output contains Sharpe, Sortino, Calmar, and Max Drawdown.
- Run `python scripts/test_tearsheet_generator.py` and confirm 100% pass rate.

## Related Skills

- `benchmark-relative-performance-attribution`
- `paper-to-live-promotion-checklist`
---
