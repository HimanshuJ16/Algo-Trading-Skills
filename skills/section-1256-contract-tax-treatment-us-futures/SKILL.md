---
name: section-1256-contract-tax-treatment-us-futures
description: >-
  Production-grade IRS Section 1256 contract tax accounting engine calculating the 60/40 statutory capital gains split (60% long-term / 40% short-term), year-end Mark-to-Market unrealized PnL valuation, and IRS Form 6781 tax reporting for futures and broad index options.
domain: Tax & Financial Accounting
subdomain: Derivatives Tax & IRS Section 1256
tags: ["section-1256", "60-40-rule", "mark-to-market", "form-6781", "futures-taxation", "index-options"]
brokers_frameworks: ["IRS Section 1256 Tax Rules", "IRS Form 6781", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when calculating tax liabilities or optimizing year-end tax strategies for US futures contracts (CME, ICE, CBOE), broad-based index options (SPX, NDX, RUT, VIX), foreign currency contracts, or dealer equity options under IRS Section 1256. Section 1256 contracts enjoy a statutory tax advantage: 60% of net PnL is taxed at long-term capital gains rates (max 20%), while 40% is taxed at short-term rates (max 37%), resulting in an effective maximum blended rate of 26.8% (saving up to 10.2% vs standard 37% short-term rates).

## Prerequisites

- Trade log payload (`Section1256Trade`: `trade_id`, `symbol`, `contract_type`, `realized_pnl_usd`, `unrealized_mtm_pnl_usd`, `is_open_at_year_end`).
- Qualified contract types (`REGULATED_FUTURES`, `BROAD_INDEX_OPTIONS`, `FOREIGN_CURRENCY_CONTRACT`, `DEALER_EQUITY_OPTION`).

## Workflow

1. **Section 1256 Eligibility Screening**:
   - Filter trades: include regulated futures and broad-based index options; exclude single-stock/ETF options (`NON_QUALIFYING_SINGLE_STOCK`).
2. **Year-End Mark-to-Market Valuation**:
   - Mark open contracts to market at fair value on the last business day of the tax year.
3. **Statutory 60/40 Split Calculation**:
   - Net total PnL ($\text{Realized} + \text{MTM Unrealized}$).
   - Allocate 60% to Long-Term Capital Gains/Losses and 40% to Short-Term Capital Gains/Losses.
4. **Form 6781 Tax Report Generation**:
   - Output structured `Form6781TaxSummary` with estimated blended tax and quantified tax savings.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Misclassifying Single-Stock Options as Section 1256**: Claiming 60/40 tax treatment for AAPL or QQQ options (only broad index options like SPX or NDX qualify).
- **Ignoring Dec 31 Mark-to-Market Valuation**: Failing to calculate unrealized PnL on open futures contracts as of the last trading day of the tax year.
- **Overlooking Form 6781 Reporting**: Reporting Section 1256 contracts directly on Form 1099-B / Schedule D without filing IRS Form 6781.

## Verification

- Instantiate `Section1256ContractTaxTreatmentUsFuturesEngine`. Add $100,000 CME E-mini futures realized PnL $\implies$ verify 60% ($60,000) allocated to Long-Term and 40% ($40,000) to Short-Term with $10,200 tax savings vs ordinary rate. Add SPX option open at year-end with $30,000 MTM unrealized gain $\implies$ verify MTM gain included in 60/40 calculation. Add single-stock option $\implies$ verify excluded from 60/40 calculation.
- Run `python scripts/test_section_1256_contract_tax_treatment_us_futures.py`.

## Related Skills

- `fifo-vs-specific-lot-tax-accounting-methods`
- `wash-sale-rule-tracking-us`
---
