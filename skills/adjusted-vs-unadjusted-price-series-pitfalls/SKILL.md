---
name: adjusted-vs-unadjusted-price-series-pitfalls
description: Audits historical price and volume series for corporate action discontinuities
  (splits/dividends) to prevent look-ahead bias and false signals in backtesting pipelines.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- price-adjustment
- stock-splits
- dividends
- data-integrity
- look-ahead-bias
brokers_frameworks:
- Price Adjustment Auditor
- Python
version: '1.1'
author: System
license: MIT
---

## When to Use

Invoke this skill when loading raw historical OHLCV data for backtesting. Mixing adjusted and unadjusted data, or using backward-adjusted dividend data, introduces severe look-ahead bias and false signals. A stock split in unadjusted data creates a phantom -50% price drop (triggering false momentum signals), while unadjusted volume breaks liquidity filters.

## Prerequisites

- Historical OHLCV series.
- Corporate action event log (splits, dividends).

## Workflow

1. **Discontinuity Detection**: Scan for overnight price jumps $\ge 30\%$. 
2. **Volume Consistency Check**: If a split is detected, verify that the volume series was scaled inversely. (e.g., if price drops by half, volume must double to preserve notional traded).
3. **Adjustment Application**: 
   - **Splits**: Apply backward-adjustment to both Price (divide by ratio) and Volume (multiply by ratio).
   - **Dividends**: Flag as cash-inflow events (Total Return) rather than backward-adjusting historical prices, which prevents look-ahead bias.

## Common Pitfalls

- **Look-Ahead Bias via Dividends**: Using data where historical prices are backward-adjusted for dividends. This injects future yield information into past prices.
- **Volume Neglect**: Adjusting price for a stock split but forgetting to multiply historical volume by the split ratio, breaking volume-weighted indicators.
- **Double Adjustment**: Applying split factors to a series that the data vendor already adjusted.

## Verification

Run the unit tests to verify that both price and volume are adjusted correctly, and that look-ahead bias warnings are generated for improper dividend handling.

## Related Skills

- `backtest-determinism-and-reproducibility`
- `corporate-action-adjusted-backtesting`
