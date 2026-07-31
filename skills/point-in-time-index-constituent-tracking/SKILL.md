---
name: point-in-time-index-constituent-tracking
description: >-
  Point-in-Time (PIT) index constituent tracking engine maintaining historical addition/deletion membership windows, eliminating survivorship bias in equity backtests.
domain: Data Management Global
subdomain: Index Membership & Backtest Parity Architecture
tags: ["pit", "point-in-time", "index-constituents", "survivorship-bias", "sp500", "backtest-parity", "corporate-actions"]
brokers_frameworks: ["S&P / MSCI / FTSE Index Feeds", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building equity universe selectors for quantitative backtesting. Applying today's static index constituents (e.g., S&P 500 in 2026) to historical backtest dates (e.g. 2015) introduces severe survivorship bias (artificially inflating annual returns by 1-4%) by omitting delisted/bankrupt companies (e.g., Enron, Lehman Brothers) and including future additions (e.g., Tesla). This engine reconstructs exact historical point-in-time index universes using historical addition and deletion events.

## Prerequisites

- Constituent addition and deletion log events (`index_name`, `symbol`, `event_type`: `'ADDITION'`/`'DELETION'`, `effective_date`, `weight`).
- As-of index query (`index_name`, `as_of_date`).

## Workflow

1. **Constituent Event Log Processing**:
   - Ingest chronological addition and deletion events per index.
2. **Point-in-Time Membership Query**:
   - Filter active constituents on query date $T_{\text{as\_of}}$:
     $$\text{Universe}(T) = \{ s \mid s.\text{add\_date} \le T \text{ AND } (s.\text{del\_date} \text{ IS NULL OR } s.\text{del\_date} > T) \}$$
3. **Survivorship Bias Audit**:
   - Compare PIT universe against static current universe to flag delisted "ghost" constituents.
4. **Audit Report Generation**: Output structured `PITIndexReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Today's Index Constituents**: Backtesting 2015 strategies using 2026 index membership, introducing severe survivorship bias.
- **Ignoring Delisting Exit Prices**: Assuming delisted stocks exit at final trade price without modeling zero-value bankruptcy write-offs.
- **Symbol Re-Use Errors**: Confusing historical ticker symbols reassigned to different companies over time.

## Verification

- Instantiate `PointInTimeIndexConstituentTrackingEngine`. Ingest addition of `ENRON` on 1990-01-01 and deletion on 2001-12-02. Ingest addition of `TSLA` on 2020-12-21. Query `as_of_date = 2000-06-01` $\implies$ verify `ENRON` included, `TSLA` excluded. Query `as_of_date = 2023-01-01` $\implies$ verify `TSLA` included, `ENRON` excluded.
- Run `python scripts/test_point_in_time_index_constituent_tracking.py`.

## Related Skills

- `point-in-time-fundamentals-data-joins`
- `reference-data-symbol-mapping-across-vendors`
---
