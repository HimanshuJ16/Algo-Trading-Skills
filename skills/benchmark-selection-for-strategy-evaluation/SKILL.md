---
name: benchmark-selection-for-strategy-evaluation
description: Choosing an appropriate benchmark against which to evaluate a strategy's
  risk-adjusted performance.
domain: Backtesting
subdomain: Evaluation
tags:
- backtesting
- benchmark
- evaluation
- metrics
brokers_frameworks:
- Pandas
- NumPy
version: "1.0.0"
author: System
license: MIT
---

# Benchmark Selection for Strategy Evaluation

## When to Use
Use this skill when evaluating a new strategy. Selecting the right benchmark is crucial; if you run a tech-heavy long-only strategy, benchmarking it against a risk-free rate or a generic broad index will make it look artificially good during tech bull markets.

## Prerequisites
- Time series of strategy returns.
- Time series of candidate benchmark returns (e.g., SPY, QQQ, sector ETFs, risk-free rate).

## Workflow
1. Collect daily returns for the strategy and several candidate benchmarks.
2. Initialize `BenchmarkSelector` with the benchmark returns.
3. Call `evaluate_benchmarks(strategy_returns)` to calculate correlation, Information Ratio, and Tracking Error.
4. Select the benchmark that closely matches the strategy's risk profile to isolate true alpha.

## Common Pitfalls
- **Defaulting to SPY:** Using SPY as a benchmark for everything (e.g., a bond strategy, a short-only strategy, a crypto strategy).
- **Ignoring Risk-Free Rate:** For market-neutral absolute return strategies, the risk-free rate or a very low-volatility benchmark is appropriate.

## Verification
- Verify that Information Ratio correctly rewards excess return relative to tracking error.
- Check that highly correlated benchmarks yield lower tracking error.

## Related Skills
- `backtest-reporting-standardized-tearsheet`
