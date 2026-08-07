---
name: 1099-b-and-broker-tax-reporting-reconciliation
description: Institutional-grade reconciliation engine for matching internal trade
  ledgers against broker 1099-B tax reports.
domain: tax-accounting-reporting-global
subdomain: tax-reporting
tags:
- tax
- reconciliation
- compliance
- 1099-B
brokers_frameworks: []
version: "1.0.0"
author: System
license: MIT
---

## When to Use

Use this skill to autonomously or semi-autonomously reconcile internal algorithmic trading records (P&L, cost basis, proceeds) against official broker 1099-B filings. It is critical for end-of-year (EOY) tax preparation, identifying broker reporting errors, and ensuring compliance with IRS Form 8949 requirements.

## Prerequisites

- Python 3.9+
- Standardized internal trade ledger containing realized tax lots.
- Parsed 1099-B data (CSV/JSON) from the clearing broker.
- Understanding of Trade Date vs. Settlement Date tax accounting rules.

## Workflow

1. **Ingestion**: Load internal tax lots and broker 1099-B records into memory.
2. **Normalization**: Align identifiers (CUSIP/Symbol), standardize dates, and format monetary values.
3. **Exact Matching**: Attempt 1-to-1 matching based on exact Symbol, Quantity, Date, and Cost Basis.
4. **Fuzzy/Tolerance Matching**: Reconcile remaining lots using a configurable monetary tolerance to account for commission rounding or fractional share discrepancies.
5. **Discrepancy Reporting**: Generate an exception report for unmatched lots, wash sale disagreements, or missing data.

## Common Pitfalls

- **End-of-Year Settlement Disconnects**: Trades executed on Dec 30/31 may appear on internal ledgers for the current tax year but settle in the following year, causing broker mismatches.
- **Wash Sale Adjustments**: Brokers track wash sales strictly across identical CUSIPs; internal algorithms might track them differently or trade across multiple accounts, leading to discrepancies.
- **Corporate Actions**: Stock splits or mergers can alter cost basis calculations, often resulting in rounding differences of a few pennies.

## Verification

Run the provided unit tests to verify exact matching, tolerance matching, and exception handling for missing records.

## Related Skills

- automated-tax-lot-reporting-pipeline
- wash-sale-rule-tracking-us
