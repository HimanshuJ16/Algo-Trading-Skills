---
name: mark-to-market-election-for-active-traders-us
description: >-
  US tax accounting engine evaluating IRS Section 475(f) Mark-to-Market (MTM) election for active traders, eliminating wash sale rules, marking open positions to year-end FMV, and reporting ordinary P&L on Form 4797.
domain: Tax & Accounting Global
subdomain: US Active Trader Tax & Section 475(f) MTM
tags: ["tax-accounting", "section-475f", "mark-to-market", "wash-sale-exemption", "form-4797", "trader-tax-status", "ordinary-loss"]
brokers_frameworks: ["IRS Code Section 475(f)", "Form 4797 Part II", "Form 3115", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when calculating tax liabilities for active US algorithmic traders and proprietary funds qualifying for Trader Tax Status (TTS). Under standard IRS capital accounting, wash sale rules (IRC Section 1091) disallow losses and net capital loss deductions are capped at $\$3,000$/year. Electing **IRS Section 475(f) Mark-to-Market (MTM)** transforms trading P&L into **Ordinary Gain/Loss** reported on **Form 4797 Part II**, completely waives wash sale rules, marks open year-end positions to Fair Market Value (FMV), and allows $100\%$ deduction of trading losses against ordinary income.

## Prerequisites

- Active trading records (`realized_trades`: list of `RealizedTrade`, `open_tax_lots`: list of `TaxLot`).
- MTM Election flag (`is_mtm_elected`: boolean).

## Workflow

1. **Section 475(f) MTM vs Capital Accounting Determination**:
   - Check `is_mtm_elected`.
2. **Realized & Unrealized P&L Calculation**:
   - Calculate total realized P&L from closed trades.
   - If MTM Elected: Mark open year-end lots to FMV:
     $$\text{Unrealized MTM P\&L} = \sum (\text{FMV\_Price}_i - \text{Cost\_Basis}_i) \times \text{Qty}_i$$
     Wash Sale Disallowance $= \$0.00$.
   - If Capital Accounting: Wash Sale rules apply, Unrealized MTM P&L $= \$0.00$.
3. **Tax Loss Limitation & Form Mapping**:
   - If MTM Elected: Total Taxable Income $= \text{Realized P\&L} + \text{Unrealized MTM P\&L}$ (No $\$3,000$ loss cap, mapped to **Form 4797 Part II**).
   - If Capital Accounting: Net Capital Loss capped at $-\$3,000$ against ordinary income (mapped to **Form 8949 / Schedule D**).
4. **Audit Report Generation**: Output structured `MtmTaxReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying Wash Sale Disallowance to MTM Traders**: Calculating 30-day wash sale loss deferrals for a trader with a valid Section 475(f) election, misstating tax liability.
- **Limiting Net Losses to $3,000 for MTM**: Capping MTM net trading losses at $\$3,000$ instead of fully deducting ordinary losses on Form 4797.
- **Failing to Mark Year-End Open Positions**: Forgetting to mark open positions to FMV at midnight December 31st for MTM elected accounts.

## Verification

- Instantiate `MarkToMarketTaxEngine`. Audit Trader with $-\$50,000$ Realized Loss and $+\$10,000$ open MTM Gain. With `is_mtm_elected=True` $\implies$ verify Wash Sale Disallowance $= \$0.00$, Total Reportable Ordinary Loss $= -\$40,000$ (Full deduction on Form 4797 Part II). With `is_mtm_elected=False` $\implies$ verify Reportable Loss capped at $-\$3,000$ (Schedule D).
- Run `python scripts/test_mark_to_market_election_for_active_traders_us.py`.

## Related Skills

- `wash-sale-rule-tracking-us`
- `fifo-vs-specific-lot-tax-accounting-methods`
---
