---
name: point-in-time-fundamentals-data-joins
description: >-
  Point-In-Time (PIT) fundamentals data join engine enforcing SEC EDGAR filing date timestamps, preventing restatement lookahead bias and unreleased earnings leakage in backtests.
domain: Data Management Global
subdomain: Fundamental Data & Point-in-Time Architecture
tags: ["pit", "point-in-time", "fundamentals", "sec-edgar", "filing-date", "restatement", "lookahead-bias", "as-of-join"]
brokers_frameworks: ["SEC EDGAR Public Database", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when joining fundamental financial metrics (e.g. EPS, Revenue, Debt/Equity, Free Cash Flow) from 10-K and 10-Q filings with market price data for quantitative strategy backtesting. Standard joins using `period_end_date` (e.g. fiscal quarter end Dec 31) assume earnings were available on Dec 31, creating severe lookahead bias (since SEC filings are released 45-60 days later in Feb/March). This engine executes Point-In-Time (PIT) as-of joins using `filing_date`, ensuring backtests view only data available on trading date $T$.

## Prerequisites

- Fundamental filing records (`ticker`, `metric_name`, `value`, `period_end_date`, `filing_date`, `revision_number`).
- As-of query request (`ticker`, `metric_name`, `as_of_date`).

## Workflow

1. **Filing Date Availability Filtering**:
   - Filter candidate records: $\text{filing\_date} \le T_{\text{as\_of}}$ AND $\text{period\_end\_date} \le T_{\text{as\_of}}$.
2. **Latest As-Reported Record Resolution**:
   - Select record with maximum `filing_date` published on or before $T_{\text{as\_of}}$.
3. **Restatement Leakage Audit**:
   - Detect if a naive period-end join would have fetched a future restated metric released after $T_{\text{as\_of}}$.
4. **Audit Report Generation**: Output structured `PITFundamentalsReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Joining on Period End Date**: Using Dec 31 fiscal period end date as the signal availability date instead of Feb/March SEC EDGAR filing date.
- **Ignoring Restatements & Revisions**: Overwriting original as-reported numbers with post-audit restatements released months or years later.
- **Filing Time Ambiguity**: Treating filing date as midnight UTC on filing day instead of accounting for trading hours market close ($16:00$ EST).

## Verification

- Instantiate `PointInTimeFundamentalsDataJoinsEngine`. Insert Q4 EPS for `AAPL`: original filing $\text{EPS} = \$1.50$ on Feb 15; restatement $\text{EPS} = \$1.20$ on Aug 10. Query `as_of_date = 2023-03-01` $\implies$ verify original $\$1.50$ returned (restatement blocked). Query `as_of_date = 2023-09-01` $\implies$ verify restated $\$1.20$ returned.
- Run `python scripts/test_point_in_time_fundamentals_data_joins.py`.

## Related Skills

- `point-in-time-database-for-ml-training-data`
- `reference-data-golden-source-designation`
---
