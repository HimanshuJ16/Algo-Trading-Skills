---
name: adjusted-vs-unadjusted-price-series-pitfalls
description: >-
  Use when preparing historical price data for backtesting to detect and handle split/dividend-adjusted vs unadjusted price series, preventing silent signal corruption from mixing adjustment types.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "price-adjustment", "stock-splits", "dividends", "data-integrity", "corporate-actions"]
brokers_frameworks: ["Price Adjustment Auditor", "Python"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when loading historical price data for backtesting. Mixing split-adjusted and unadjusted price series within the same backtest silently corrupts signals — a 2:1 stock split creates a phantom $-50\%$ drop in unadjusted data that triggers false mean-reversion or momentum signals. This skill audits price series for adjustment type consistency, detects corporate action discontinuities, and applies forward/backward adjustment ratios.

## Prerequisites

- Historical OHLCV price series with known adjustment type (adjusted or unadjusted).
- Corporate action event log (split ratios, ex-dividend dates, dividend amounts).

## Workflow

1. **Detect Corporate Action Discontinuities**: Scan for overnight price jumps exceeding threshold ($>30\%$) that correspond to known split/dividend dates.
2. **Classify Adjustment Type**: Determine if series is forward-adjusted, backward-adjusted, or unadjusted.
3. **Apply Adjustment Ratios**: Convert between adjusted and unadjusted using cumulative split/dividend factors.
4. **Validate Consistency**: Assert all series in a multi-asset backtest use the same adjustment type.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mixing Adjustment Types Across Symbols**: Using adjusted data for equities and unadjusted for ETFs in the same universe.
- **Computing Returns on Unadjusted Data**: Calculating daily returns across a split date, producing a phantom $-50\%$ return.
- **Double-Adjusting Already-Adjusted Data**: Applying split factors to data that a vendor has already adjusted.

## Verification

- Inject a 2:1 split into unadjusted series, verify detection and correct adjustment.
- Verify mixed adjustment type detection across multi-symbol universe.
- Run `python scripts/test_price_adjustment_auditor.py` and confirm 100% pass rate.

## Related Skills

- `data-vendor-cross-validation-for-backtests`
- `backtest-determinism-and-reproducibility`
---
