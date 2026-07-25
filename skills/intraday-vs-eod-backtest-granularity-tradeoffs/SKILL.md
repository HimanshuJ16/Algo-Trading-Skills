---
name: intraday-vs-eod-backtest-granularity-tradeoffs
description: >-
  Use when choosing appropriate data granularity (tick, minute, EOD) for a backtest based on the strategy's holding period and decision frequency to balance accuracy against compute cost.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "data-granularity", "intraday", "eod", "tick-data", "compute-cost"]
brokers_frameworks: ["Granularity Advisor Engine", "Python"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when designing a backtest framework to select the right data resolution. A swing strategy holding 5+ days wastes compute on tick data; a scalping strategy using EOD bars misses all intraday dynamics. This skill recommends optimal granularity based on strategy characteristics.

## Prerequisites

- Strategy holding period and signal frequency.
- Available data resolutions and their storage/compute costs.

## Workflow

1. **Profile Strategy Characteristics**: Classify holding period and decision frequency.
2. **Evaluate Granularity Options**: Score tick/1-min/5-min/hourly/EOD on accuracy and cost.
3. **Recommend Optimal Resolution**: Select resolution that balances fidelity and efficiency.
4. **Estimate Compute Budget**: Project CPU-hours and storage for the recommended resolution.

> Full procedure: see `references/workflows.md`.

## Common Pitfalls

- **Using Tick Data for Monthly Strategies**: Wasting 100x compute for negligible accuracy gain.
- **Using EOD for Intraday Strategies**: Missing all intraday P&L dynamics and fill simulation.

## Verification

- Run `python scripts/test_granularity_advisor.py` — 100% pass rate.

## Related Skills

- `multi-timeframe-backtest-consistency-checks`
- `backtest-infrastructure-cost-budgeting`
---
