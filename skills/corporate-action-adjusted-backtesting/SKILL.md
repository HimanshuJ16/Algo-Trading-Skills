---
name: corporate-action-adjusted-backtesting
description: >-
  Quantitative backtesting module for processing corporate action event logs (stock splits, cash dividends, reverse splits), computing Cumulative Adjustment Factors (CAF), and separating raw vs. adjusted price data.
domain: Data Management & Backtesting
subdomain: Corporate Actions
tags: ["corporate-actions", "stock-splits", "dividends", "caf", "adjusted-prices", "backtesting", "point-in-time"]
brokers_frameworks: ["Pandas", "NumPy", "Generic Backtester"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when backtesting quantitative trading strategies on historical equity or ETF data. Unadjusted raw price data contains artificial gaps caused by stock splits (e.g. a 4-for-1 split looks like a 75% price crash) and cash dividends. Using raw data directly for technical indicators (SMA, RSI, Bollinger Bands) generates false signals. Conversely, using pre-adjusted prices for trade execution causes look-ahead bias and miscalculates share counts. This module manages Cumulative Adjustment Factors (CAF) to provide adjusted prices for signals while preserving raw prices for execution.

## Prerequisites

- Corporate action event history (ex-date, action type, ratio/dividend amount).
- Historical raw price series (`open`, `high`, `low`, `close`, `volume`).

## Workflow

1. **Corporate Action Event Ingestion**: Register split events ($S_{split}$) and cash dividend events ($D$).
2. **Adjustment Factor Calculation**:
   - For Split on ex-date: Factor $\alpha_{split} = \frac{1}{\text{Split Ratio}}$.
   - For Dividend on ex-date: Factor $\alpha_{div} = 1 - \frac{D}{P_{ex\_close}}$.
3. **Cumulative Adjustment Factor (CAF) Construction**:
   - Compute backward product: $\text{CAF}_t = \prod_{\tau > t} \alpha_\tau$.
4. **Price & Volume Adjustment**:
   - Adjusted Price $P_{adj}(t) = P_{raw}(t) \times \text{CAF}_t$.
   - Adjusted Volume $V_{adj}(t) = V_{raw}(t) / \text{CAF}_t$.
5. **Backtest Processing Dual-Path**:
   - Use $P_{adj}$ for technical signal generation.
   - Use $P_{raw}$ for actual order sizing, commission calculation, and cash dividend PnL credits on ex-dates.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Look-Ahead Bias in Adjusted Prices**: Applying future dividend adjustments to past price data during point-in-time signal generation, allowing the model to "know" about future cash payouts.
- **Executing at Adjusted Prices**: Using adjusted prices to calculate trade share quantities and cash debit/credit, resulting in incorrect portfolio cash balances.
- **Volume Unadjusted**: Multiplying price by split factor without adjusting trading volume accordingly, distorting ADV liquidity checks.

## Verification

- Instantiate `CorporateActionAdjuster`. Feed a 2-for-1 stock split event on Day 5 where raw close drops from $100 to $50. Verify that historical prices for Days 1-4 are retroactively scaled down by factor 0.5 ($100 \to $50), removing the price gap. Process a $2.00 dividend on a $100 stock (factor 0.98) and verify adjusted series continuity.
- Run `python scripts/test_corporate_action_adjuster.py`.

## Related Skills

- `corporate-action-event-calendar-integration`
- `point-in-time-fundamentals-data-joins`
---
