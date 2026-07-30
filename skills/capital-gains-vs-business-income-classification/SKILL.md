---
name: capital-gains-vs-business-income-classification
description: Quantitative tax classification engine to automatically categorize trading
  activity as Capital Gains vs Business Income (Speculative / Non-Speculative) based
  on holding periods and asset class.
domain: Back-Office
subdomain: Taxation & Compliance
tags:
- tax
- capital-gains
- business-income
- speculative
- classification
brokers_frameworks:
- Generic Post-Trade
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing end-of-year post-trade data for tax reporting or when building a backtesting engine that needs to accurately model net post-tax PnL. Tax authorities (like the CRA, IRS, or CBDT) differentiate heavily between casual investing (Capital Gains) and frequent algorithmic trading (Business Income). F&O trading is typically classified differently from Intraday Equity or Long-Term Equity.

## Prerequisites

- Trade execution ledger with entry and exit timestamps.
- Asset class tags (e.g., Equity, Derivative).
- A clear definition of the local tax jurisdiction's rules regarding speculative vs. non-speculative holds.

## Workflow

1. **Trade Ingestion**: Feed closed trades into the `TaxClassificationEngine`.
2. **Holding Period Extraction**: The engine calculates the duration between the open and close timestamps.
3. **Asset Class Filtering**: 
   - Derivatives (Futures & Options) are categorically marked as *Non-Speculative Business Income*.
   - Intraday Equities (hold time < 1 day) are categorically marked as *Speculative Business Income*.
   - Equities held > 1 day are evaluated based on frequency and duration to determine if they qualify as *Capital Gains* (Short-Term or Long-Term).
4. **Aggregation**: Output a classified PnL ledger to map against the appropriate tax brackets.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mixing Classifications**: Randomly switching between treating a strategy as Business Income one year and Capital Gains the next; this triggers regulatory audits.
- **Ignoring Wash Sales**: Failing to account for superficial loss rules or wash sale rules when treating activity as Capital Gains.
- **Deducting Expenses Improperly**: Deducting server and data costs against Capital Gains instead of Business Income.

## Verification

- Simulate an intraday equity trade, an options trade, and a 2-year equity hold. Verify the engine correctly outputs Speculative Business, Non-Speculative Business, and LTCG respectively.
- Run `python scripts/test_capital_gains_vs_business_income_classification.py`.

## Related Skills

- `canada-iiroc-electronic-trading-rules`
- `best-execution-record-keeping-global`
