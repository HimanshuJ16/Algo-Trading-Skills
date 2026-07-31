---
name: intraday-vs-eod-backtest-granularity-tradeoffs
description: >-
  Quantitative backtest engineering advisor for evaluating data resolution tradeoffs (Tick vs 1-Min vs Daily EOD), estimating storage/compute footprints, and eliminating OHLC in-bar order sequence biases.
domain: Quant Research & Alt Data
subdomain: Backtesting Engine Design & Data Granularity
tags: ["backtesting", "data-granularity", "ohlc-bias", "intraday-vs-eod", "tick-data", "compute-footprint", "simulation-bias"]
brokers_frameworks: ["Vectorized / Event-Driven Backtesters", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing quantitative backtesting engines, selecting market data subscriptions, and auditing strategy simulation fidelity. Choosing backtest data resolution involves a fundamental engineering tradeoff: **Daily EOD data** is ultra-fast and storage-efficient ($< 1\text{ MB}$) but introduces catastrophic **OHLC in-bar sequence bias** for intraday stop-loss strategies (assuming take-profit hit before stop-loss). Conversely, **Tick / L2 data** eliminates bias but demands massive storage ($> 500\text{ GB}$) and compute. This module advises optimal data granularity and quantifies storage/compute footprints.

## Prerequisites

- Strategy profile parameters (`holding_period`, `trade_frequency_per_day`, `has_intraday_stop_loss`, `universe_size`, `history_years`).
- Target storage and compute memory constraints.

## Workflow

1. **Strategy Profile Ingestion**:
   - Ingest strategy characteristics (`holding_period = 'INTRADAY_MINUTES'`, `has_intraday_stop_loss = True`).
2. **OHLC In-Bar Execution Bias Audit**:
   - If `has_intraday_stop_loss == True` and user uses `DAILY_EOD` data $\implies$ Flag **OHLC Sequence Ambiguity Bias Warning** (High/Low order sequence cannot be determined within daily bar).
3. **Granularity Recommendation**:
   - High Frequency ($> 20$ trades/day) $\implies$ `TICK_L2`.
   - Intraday ($< 20$ trades/day or intraday stops) $\implies$ `INTRADAY_1MIN`.
   - Long-term Trend / Positional $\implies$ `DAILY_EOD`.
4. **Compute & Storage Footprint Estimation**:
   - Estimate total data points and storage size in GB across 5 years.
5. **Audit Report Generation**: Output structured `BacktestGranularityReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Backtesting Intraday Stops on EOD Data**: Simulating intraday stop-loss and take-profit orders on Daily EOD OHLC bars, incorrectly assuming take-profit hit first and producing fake backtest Sharpe ratios ($> 4.0$).
- **Using Tick Data for Multi-Year Positional Strategies**: Loading 500 GB of tick data for a 3-month holding period momentum strategy, suffering 100x compute slowdown without improving accuracy.
- **Ignoring Fill Queue & Spread Dynamics**: Assuming 100% fill rate at Mid-price on 1-Minute bars for high-frequency market making algorithms.

## Verification

- Instantiate `BacktestGranularityAdvisorEngine`. Test Intraday Scalper (`holding_period='INTRADAY_MINUTES'`, `trades_per_day=30`, `has_stop_loss=True`, `universe=500`, `years=5`) $\implies$ verify engine recommends `TICK_L2` / `INTRADAY_1MIN` and estimates $\sim 19.5\text{ GB}$ storage. Test Flawed EOD Config (`has_stop_loss=True` with `DAILY_EOD`) $\implies$ verify `OHLC_SEQUENCE_BIAS_WARNING`.
- Run `python scripts/test_granularity_advisor.py`.

## Related Skills

- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `research-environment-vs-production-environment-parity`
---
