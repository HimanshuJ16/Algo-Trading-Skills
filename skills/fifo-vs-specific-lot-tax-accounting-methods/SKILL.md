---
name: fifo-vs-specific-lot-tax-accounting-methods
description: >-
  Quantitative tax accounting engine for matching open tax lots across FIFO, LIFO, HIFO, and Specific Identification methods, calculating realized short-term vs long-term capital gains, and optimizing tax liabilities.
domain: Tax Accounting & Reporting
subdomain: Tax Lot Matching & Capital Gains Accounting
tags: ["tax-accounting", "fifo", "hifo", "lifo", "specific-identification", "capital-gains", "stcg", "ltcg", "tax-lot-matching"]
brokers_frameworks: ["IRS Tax Lot Guidelines", "Global Accounting Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in post-trade processing systems, tax-loss harvesting engines, and institutional tax accounting pipelines. When liquidating positions built across multiple purchase dates and prices, the choice of tax lot matching methodology (**FIFO**, **LIFO**, **HIFO**, or **Specific Identification**) directly dictates realized Short-Term Capital Gains (STCG, $\le 365$ days) vs Long-Term Capital Gains (LTCG, $> 365$ days). This module processes sell transactions against open tax lot inventories, executing deterministic lot matching and calculating net tax liabilities.

## Prerequisites

- Open tax lot inventory for each asset (`lot_id`, `acquisition_date`, `qty`, `cost_basis_per_share`, `holding_period_days`).
- Sell order execution details (`sale_qty`, `sale_price`, `sale_date`).
- Desired matching strategy (`FIFO`, `LIFO`, `HIFO`, `SPECIFIC_LOT`).

## Workflow

1. **Tax Lot Ordering & Selection**:
   - Sort open tax lots based on active matching strategy:
     - `FIFO`: Sort ascending by `acquisition_date` (oldest first).
     - `LIFO`: Sort descending by `acquisition_date` (newest first).
     - `HIFO`: Sort descending by `cost_basis_per_share` (highest cost first).
     - `SPECIFIC_LOT`: Match against explicit `target_lot_id`.
2. **Partial & Full Lot Depletion**:
   - Match sell order quantity against ordered tax lots, updating remaining lot quantities.
3. **Gain / Loss & Holding Period Classification**:
   - Realized Gain/Loss = $(\text{Sale Price} - \text{Cost Basis}) \times \text{Matched Shares}$.
   - Classify as `LTCG` if `holding_period_days > 365`, else `STCG`.
4. **Audit Report Generation**: Output structured `TaxLotAccountingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Defaulting to FIFO in High-Tax Regimes**: Using default FIFO during market rallies, realizing large short-term capital gains when HIFO or Specific Lot identification would minimize tax liabilities.
- **Failing to Document Specific Lot Designations Pre-Settlement**: Realizing specific identification trades without generating contemporaneous trade confirmations prior to settlement date, violating IRS rules.
- **Miscalculating Holding Periods Across Leap Years**: Incorrectly calculating the 365-day threshold for long-term capital gains classification.

## Verification

- Instantiate `TaxLotAccountingEngine`. Ingest 3 tax lots: Lot A (\$100, 400 days old), Lot B (\$150, 100 days old), Lot C (\$120, 50 days old). Process 100 share sell at \$140. Test FIFO $\implies$ matches Lot A, realizes $+\$40.00$ LTCG. Test HIFO $\implies$ matches Lot B, realizes $-\$10.00$ STCG (tax loss). Test Specific Lot $\implies$ matches Lot C, realizes $+\$20.00$ STCG.
- Run `python scripts/test_fifo_vs_specific_lot_tax_accounting_methods.py`.

## Related Skills

- `crypto-transaction-tax-lot-tracking`
- `wash-sale-rule-tracking-us`
---
