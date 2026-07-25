---
name: backtest-database-schema-for-point-in-time-queries
description: >-
  Use when designing a database schema that natively supports point-in-time queries to make lookahead-bias mistakes structurally harder to introduce in backtests.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "point-in-time", "database-schema", "lookahead-bias", "temporal-queries"]
brokers_frameworks: ["Point-in-Time Schema Engine", "Python"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building data infrastructure for backtesting. Standard database tables return the latest value for a query, silently introducing lookahead bias. A point-in-time (PIT) schema stores records with `known_at` timestamps, ensuring queries like "what was the P/E ratio of AAPL as known on 2023-01-15?" return only data available at that historical moment.

## Prerequisites

- Database or data store with temporal versioning capability.
- Historical fundamental/reference data with publication timestamps.

## Workflow

1. **Design PIT Schema**: Add `known_at` / `valid_from` columns to every fact table.
2. **Query with As-Of Semantics**: Filter records by `known_at <= query_date`.
3. **Validate No Future Leakage**: Audit that no record with `known_at > backtest_date` is returned.
4. **Index for Performance**: Create composite indexes on `(symbol, known_at)`.

> Full procedure: see `references/workflows.md`.

## Common Pitfalls

- **Using `created_at` Instead of `known_at`**: Database insertion time != when data was publicly available.
- **Restated Earnings Without Versioning**: Overwriting Q1 earnings with restated figures without preserving original.

## Verification

- Run `python scripts/test_pit_schema.py` — 100% pass rate.

## Related Skills

- `backtest-look-ahead-in-universe-selection`
- `backtest-determinism-and-reproducibility`
---
