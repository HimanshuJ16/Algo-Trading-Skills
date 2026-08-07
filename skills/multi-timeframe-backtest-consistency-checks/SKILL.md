---
name: multi-timeframe-backtest-consistency-checks
description: Use when verifying that a strategy's signals computed from higher-resolution
  data (e.g. 1-min bars) produce consistent results when compared against resampled
  lower-resolution data (e.g. 5-min, 15-min bars).
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- multi-timeframe
- resampling
- signal-consistency
- data-resolution
brokers_frameworks:
- Timeframe Consistency Checker
- Python
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a strategy uses indicators computed on resampled bars. If a 20-period SMA on 5-min bars diverges significantly from the same SMA computed on tick data resampled to 5-min, the resampling logic or data alignment has a bug. This skill compares signal outputs across timeframe resolutions.

## Prerequisites

- High-resolution bar data (e.g. 1-min).
- Resampling function to produce lower-resolution bars.

## Workflow

1. **Resample High-Res to Lower-Res**: Aggregate OHLCV bars from 1-min to 5-min/15-min.
2. **Compute Signals on Both**: Run indicator on native and resampled data.
3. **Measure Signal Divergence**: Compare signal values at matching timestamps.
4. **Flag Inconsistencies**: Alert if divergence exceeds tolerance.

> Full procedure: see `references/workflows.md`.

## Common Pitfalls

- **Boundary Bar Misalignment**: Resampling 1-min to 5-min with wrong boundary anchor.
- **Volume Double-Counting**: Summing volume incorrectly during OHLCV aggregation.

## Verification

- Run `python scripts/test_timeframe_consistency.py` — 100% pass rate.

## Related Skills

- `intraday-vs-eod-backtest-granularity-tradeoffs`
- `backtest-determinism-and-reproducibility`
---
