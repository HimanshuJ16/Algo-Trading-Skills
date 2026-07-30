---
name: benchmark-portfolio-for-multi-strategy-performance-context
description: Use when evaluating a multi-strategy quantitative portfolio to isolate
  genuine skill (Alpha) from hidden market exposure (Beta) and calculate tracking
  error against a custom or standard benchmark.
domain: algorithmic-trading
subdomain: portfolio-construction
tags:
- multi-strategy
- benchmarking
- alpha
- beta
- information-ratio
- tracking-error
brokers_frameworks:
- NumPy
- Portfolio Benchmarking
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Multi-strategy quantitative portfolios are typically designed to generate uncorrelated absolute returns. However, poor strategy allocation often results in "Hidden Beta," where the portfolio is actually just riding the S&P 500 upwards. Invoke this skill to mathematically decompose portfolio returns against a benchmark, explicitly calculating Beta, annualized Alpha, Tracking Error, and the Information Ratio to prove that the returns are driven by skill rather than simple market exposure.

## Prerequisites

- A 1D array of daily portfolio returns.
- A 1D array of daily benchmark returns over the exact same time horizon.
- The annual risk-free rate (e.g., 0.04).

## Workflow

1. **Calculate Active Return**: Subtract benchmark returns from portfolio returns.
2. **Calculate Tracking Error**: Compute the standard deviation of the active return (annualized).
3. **Calculate Beta**: Compute the covariance of portfolio and benchmark returns, divided by the variance of the benchmark.
4. **Calculate Alpha (Skill)**: Calculate the residual return not explained by Beta.
5. **Calculate Information Ratio**: Divide the annualized active return by the annualized tracking error.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Inappropriate Benchmarking**: Benchmarking a market-neutral statistical arbitrage strategy against a long-only Equity Index. (Use a zero-beta custom benchmark or a cash benchmark).
- **Ignoring Tracking Error**: Focusing entirely on outperforming the benchmark while ignoring that the portfolio's tracking error has exploded to 20%, fundamentally violating the fund's mandate.

## Verification

- Simulate a portfolio that is perfectly correlated to the benchmark with a 2x leverage factor. The Beta must calculate to 2.0, and Alpha must be 0.0.
- Run `python scripts/test_multi_strategy_benchmarker.py` and confirm 100% pass rate.

## Related Skills

- `cross-strategy-correlation-monitoring`
- `benchmark-relative-performance-attribution`
