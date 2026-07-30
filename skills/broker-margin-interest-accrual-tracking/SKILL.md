---
name: broker-margin-interest-accrual-tracking
description: Institutional-grade margin interest and short borrow fee tracker. Uses
  progressive blended rate schedules (e.g. IBKR style), accounts for 360-day vs 365-day
  conventions, applies weekend T+1/T+2 compounding rules, and deducts cost of leverage
  and borrow from net P&L.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- margin-interest
- accrual-tracking
- borrowing-cost
- pnl-accounting
- leverage-cost
- institutional
brokers_frameworks:
- Margin Cost Tracker
- Python PnL Accounting
- Interactive Brokers
version: '2.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when performing highly accurate institutional P&L accounting for algorithmic strategies that use leverage, hold overnight positions, or engage in short selling. Standard retail backtests ignore financing costs, leading to massive overestimations of Sharpe and net return—especially in elevated interest rate environments (5-8% APR). This skill calculates exact daily interest and short borrow accruals using institutional progressive tiered schedules and accurate day count conventions.

## Prerequisites

- Broker margin interest schedule (blended progressive tiers).
- Short stock hard-to-borrow (HTB) rates.
- Day-count conventions (typically 360 days for US broker margin and borrow fees, 365 days for some international).
- Accurate daily EOD cash and short market values.

## Workflow

1. **Configure Rate Schedules**: Set up the progressive tiers representing your broker's lending rates (e.g., 6.83% for first $100k, 6.33% for next $900k, etc.).
2. **Track Daily Debit Balance & Short Market Value**: Determine end-of-day balances subject to financing.
3. **Apply Blended Rate Calculation**: Calculate the effective APR for the total balance by filling each tier progressively.
4. **Compute Daily Cost with Weekend Logic**: Apply daily rates (using `rate / 360`). For positions held over Friday night, apply 3 days of interest (covering Saturday and Sunday).
5. **Adjust P&L**: Subtract total financing costs (margin interest + borrow fees) from Gross P&L to derive exact Adjusted Net P&L.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Blended Rates**: Assuming the lowest tier's rate applies to the entire balance, whereas brokers charge progressively.
- **Wrong Day-Count**: Using 365 days instead of the industry standard 360 days for US dollar financing, understating costs by ~1.4%.
- **Missing Weekend Compounding**: Forgetting that overnight margin held Friday incurs 3 days of interest.
- **Conflating Borrow Fees with Margin**: Shorting a stock incurs a borrow fee (based on the short market value) AND if it causes a cash debit, incurs margin interest as well.

## Verification

- Simulate a $150k margin balance across the weekend and verify the blended APR and 3-day accrual logic.
- Run `python scripts/test_margin_interest.py` and confirm all tests pass 100%.


## Related Skills

Documentation for Related Skills.
