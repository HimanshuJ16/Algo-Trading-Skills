---
name: wash-sale-rule-tracking-us
description: "Institutional tax accounting skill for tracking US IRS 26 U.S. Code § 1091 Wash Sale rules, scanning the 61-day window (30 days prior, trade date, 30 days post), matching replacement shares, disallowing loss deductions, adjusting replacement cost basis, and generating Form 1099-B Box 1g audit disclosures."
domain: Global Tax Accounting & Regulatory Reporting
subdomain: US IRS Tax Compliance (IRC § 1091)
tags:
- wash-sale
- irs-section-1091
- cost-basis-adjustment
- form-1099-b
- capital-loss-disallowance
- tax-lots
- fifo-matching
brokers_frameworks:
- us-irc-1091
- form-1099-b
- finra
- sec
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when processing active trading executions for US taxable accounts, calculating annual capital gains/losses, or generating IRS Form 1099-B Box 1g disclosures.

This skill provides institutional mechanisms to:
- Enforce **IRC § 1091 61-Day Window Scanning** ($[\text{Loss Date} - 30\ \text{days},\; \text{Loss Date} + 30\ \text{days}]$).
- Match FIFO loss executions against replacement buy tax lots.
- Calculate **Disallowed Wash Sale Losses** and disallow immediate tax deductions.
- Compute **Adjusted Replacement Cost Basis** ($\text{Basis}_{\text{new}} = \text{Price}_{\text{buy}} + \frac{\text{Disallowed Loss}}{\text{Shares}}$).
- Process **Partial Wash Sales** when replacement share quantities differ from loss share quantities.
- Format aggregated **Form 1099-B Tax Disclosures** (Gross Realized PnL, Disallowed Loss, Net Taxable PnL).

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `typing`).
- Chronological trade execution history (trade ID, symbol, trade date, side, price, quantity).

## Workflow

1. **Ingest Trade Executions**: Construct `TradeExecution` instances detailing trade ID, symbol, trade date, side (`BUY` or `SELL`), execution price, and quantity.
2. **Register Trades**: Call `add_trade(trade)` to populate the chronological tax ledger.
3. **Execute Wash Sale Audit**: Invoke `evaluate_wash_sales_for_symbol(symbol)` to execute FIFO tax lot matching and scan the 61-day window.
4. **Adjust Replacement Cost Basis**: The engine identifies replacement buys and adds disallowed losses to the replacement share cost basis.
5. **Generate Form 1099-B Report**: Retrieve `WashSaleSummary` containing total realized gross PnL, disallowed wash losses, and net allowed taxable PnL.

## Common Pitfalls

- **30-Day Window Misunderstanding**: The 61-day window includes **30 days BEFORE the sale date, the sale date itself, AND 30 days AFTER the sale date**. Buying replacement shares BEFORE selling at a loss triggers a wash sale.
- **Cross-Account Wash Sales**: The IRS applies wash sale rules across ALL accounts owned by a taxpayer (including IRAs and spouse accounts). Executing a loss in a taxable account and buying the same stock in an IRA disallows the loss PERMANENTLY with no basis adjustment.
- **Option-to-Stock Wash Sales**: Buying an in-the-money call option or selling a put option within the 61-day window replaces stock loss shares, triggering a wash sale.
- **Year-End Wash Sale Traps**: Selling at a loss in December and buying back in January within 30 days defers the loss into the new tax year instead of recognizing it in the current year.

## Verification

Run the unit test suite to validate 61-day window scanning, post-loss replacement buys, pre-loss replacement buys, partial wash sales, and cost basis adjustments:

```bash
python -m unittest discover -s skills/wash-sale-rule-tracking-us/scripts
```

## Related Skills

- `vat-gst-treatment-of-trading-related-services`
- `transfer-pricing-considerations-for-multi-entity-trading-operations`
- `transfer-pricing-considerations-for-multi-entity-trading-operations`
- `third-party-custody-audit-report-review-cadence`

