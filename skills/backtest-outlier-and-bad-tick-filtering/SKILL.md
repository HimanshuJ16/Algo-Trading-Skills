---
name: backtest-outlier-and-bad-tick-filtering
description: >-
  Use when cleaning historical tick and bar data for backtesting to detect and filter erroneous price prints, fat-finger quotes, stale prices, and out-of-sequence ticks before they distort signal generation.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "bad-tick-filtering", "outlier-detection", "data-cleaning", "median-filter", "price-spikes"]
brokers_frameworks: ["Outlier Bad Tick Filter Engine", "Python"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill during data ingestion prior to backtesting. Raw exchange tick files frequently contain anomalous data points: fat-finger bad prints ($10.00$ on a $100.00$ stock), test messages, corrupt quotes during opening auction, or zero-price trades. If unfiltered, a single erroneous low print can trigger a false $+900\%$ momentum signal or hit a stop loss in backtest simulation that could never occur in reality.

## Prerequisites

- Raw price time series (ticks or high-frequency bars).
- Rolling window size $W$ (e.g., 21 ticks) and outlier threshold $Z_{\text{max}}$ (e.g., 5.0 MAD deviations).

## Workflow

1. **Compute Rolling Median & Median Absolute Deviation (MAD)**:
   $$\text{MAD} = \text{median}(|P_i - \text{median}(P)|)$$

2. **Evaluate Modified Z-Score**:
   $$Z_i = \frac{0.6745 \cdot |P_i - \text{median}(P)|}{\text{MAD}}$$

3. **Filter Outliers**: Drop or interpolate price prints where $Z_i > Z_{\text{max}}$ or price change $> \Delta_{\text{max}}\%$ in single tick.

4. **Generate Data Cleanliness Report**: Report total raw ticks, bad ticks purged, and percent dataset sanitized.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Mean/StdDev Instead of Median/MAD**: Outliers heavily distort mean and standard deviation, masking subsequent bad prints.
- **Over-filtering Real Volatility Spikes**: Setting $Z_{\text{max}}$ too low (e.g. 2.0) and truncating genuine flash crash price moves.

## Verification

- Inject bad tick ($P=10.0$ into $100.0$ series), verify bad tick detection and removal.
- Run `python scripts/test_outlier_filter.py` and confirm 100% pass rate.

## Related Skills

- `data-vendor-cross-validation-for-backtests`
- `backtest-determinism-and-reproducibility`
---
