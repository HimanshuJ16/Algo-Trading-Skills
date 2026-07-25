---
name: broker-margin-interest-accrual-tracking
description: >-
  Use when calculating live and backtest P&L for leveraged or short positions to track daily accrued margin interest, apply tiered APR rate schedules, and deduct interest compounding costs from strategy net returns.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "margin-interest", "accrual-tracking", "borrowing-cost", "pnl-accounting", "leverage-cost"]
brokers_frameworks: ["Margin Cost Tracker", "Python PnL Accounting"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing leveraged long strategies or short equity/futures positions held overnight across margin-enabled brokerage accounts (e.g., IBKR, Schwab, E*TRADE). Standard naive P&L tracking ignores daily margin interest, overestimating net strategy profitability — particularly during high interest rate environments (e.g. 5% to 8% APR). This skill calculates exact daily interest accruals using tiered broker rate schedules and deducts interest expenses from net strategy P&L.

## Prerequisites

- Broker margin interest APR schedule (tiered by debit balance size).
- Daily cash debit balance and short position market values.
- Day-count convention (360 days for US equities/FX, 365 days for international).

## Workflow

1. **Configure Broker Margin Tier APR Schedule**:
   - Register rate tiers (e.g. $0–$100k @ 6.50% APR; $100k–$1M @ 5.80% APR).

2. **Calculate Daily Margin Debit Balance**:
   - Compute daily debit balance $B_t = \max(0, -\text{CashBalance}_t) + \text{ShortMarketValue}_t$.

3. **Compute Daily Accrued Interest**:
   - Apply daily rate for day $t$:
     $$I_t = B_t \times \frac{\text{APR}(B_t)}{365}$$

4. **Deduct Interest from Net Strategy P&L**:
   - Accrue cumulative interest charges into position ledger:
     $$\text{NetPnL}_{\text{adjusted}} = \text{GrossPnL} - \sum I_t$$

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Weekend Interest Charges**: Margin interest accrues 7 days a week, meaning Friday night positions incur 3 days of interest (Friday, Saturday, Sunday).
- **Using Flat APR Across Tiers**: Applying a single average APR instead of tier-discounted rates on large margin balances.
- **Conflating Borrow Fees with Margin Interest**: Short selling incurs both hard-to-borrow fees AND margin debit interest.

## Verification

- Simulate 30-day overnight margin borrow of $200,000 at 6.5% APR and verify exact daily accrual and total cost deduction.
- Verify weekend (3-day) interest compounding logic.
- Run `python scripts/test_margin_interest.py` and confirm 100% pass rate.

## Related Skills

- `locate-and-borrow-cost-integration-for-shorts`
- `broker-account-margin-call-handling`
- `multi-currency-pnl-and-fx-conversion`
---
