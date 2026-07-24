---
name: corporate-action-adjusted-backtesting
description: >-
  Use when processing historical market data for backtesting to apply backward price/volume split adjustments, process ex-dividend cash flows, and prevent double-adjustment errors
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "corporate-actions", "stock-splits", "dividends", "price-adjustment"]
brokers_frameworks: ["Center for Research in Security Prices (CRSP)", "Yahoo Finance Data", "Interactive Brokers Historical API", "Polygon.io"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever backtesting trading strategies over historical stock or ETF datasets that experience corporate actions (stock splits, reverse splits, cash dividends, and stock dividends). Naively running technical indicators or stop-loss orders on unadjusted raw prices creates artificial price shocks (e.g. a 4-for-1 split drops price from $400 to $100 overnight, triggering false stop-loss exits). Conversely, applying dividend adjustments to already split-adjusted data causes double-counting errors. Implementing backward adjustment ratio trees and ex-date cash dividend crediting is mandatory.

## Prerequisites

- Unadjusted raw historical OHLCV bar series.
- Corporate action event registry containing `ex_date`, `action_type`, `ratio`, and `cash_amount`.
- Position tracking engine for ex-date dividend cash credit calculation.

## Workflow

1. **Register Corporate Action Events**:
   - Store events per symbol with `ex_date`, `action_type` (`SPLIT`, `REVERSE_SPLIT`, `DIVIDEND`), `ratio`, and `cash_amount`.

2. **Compute Cumulative Backward Adjustment Factors**:
   - Iterate backwards from current time $T_{\text{today}}$ to history $T_0$.
   - On split event (ratio $R$): Cumulative Price Factor $F_p = F_p \cdot \frac{1}{R}$; Volume Factor $F_v = F_v \cdot R$.
   - On dividend event (cash $D$, pre-ex price $P$): Dividend Factor $F_d = F_d \cdot \left(1 - \frac{D}{P}\right)$.

3. **Adjust Historical OHLCV Series**:
   - Adjusted Price: $P_{\text{adj}} = P_{\text{raw}} \cdot F_p \cdot F_d$.
   - Adjusted Volume: $V_{\text{adj}} = V_{\text{raw}} / F_p$.

4. **Process Ex-Date Cash Dividend Credit**:
   - On ex-dividend date $T_{\text{ex}}$, credit account cash balance: $\text{Cash} = \text{Open Position Quantity} \cdot D$.

5. **Double-Adjustment Prevention Guard**:
   - Verify input series is unadjusted raw data before applying adjustment factors to prevent double-adjusting.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Double Adjustment**: Applying backward split adjustments to historical data that was already split-adjusted by the data vendor.
- **Un-Adjusted Volume**: Adjusting historical prices for a stock split without adjusting historical volume proportionally ($V_{\text{adj}} = V_{\text{raw}} \times \text{Split Ratio}$).
- **Ignoring Dividend Cash Flow**: Adjusting prices downward for dividends without crediting cash to long position holders.

## Verification

- Submit 2-for-1 split event on $T_{\text{ex}}$ and verify pre-ex historical prices are halved and volumes are doubled.
- Submit cash dividend event ($1.00/share) for open position of 500 shares and verify $500.00 cash credit on ex-date.
- Verify `CorporateActionAdjuster` detects pre-adjusted data and blocks double-adjustment.
- Run unit test suite `python scripts/test_corporate_action_adjuster.py` and confirm 100% pass rate.

## Related Skills

- `survivorship-bias-free-universe-construction`
- `walk-forward-optimization-window-management`
- `purge-and-embargo-cross-validation`
---
