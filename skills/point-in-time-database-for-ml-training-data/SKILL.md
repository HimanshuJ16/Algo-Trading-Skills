---
name: point-in-time-database-for-ml-training-data
description: Use when constructing ML training datasets to build a point-in-time correct
  feature store database, ensuring feature values joined to historical labels reflect
  only information available at target timestamp T.
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- point-in-time-db
- training-data
- feature-store
- data-leakage-prevention
- as-of-join
brokers_frameworks:
- Point-In-Time ML Database Engine
- Python Pandas
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building training datasets for ML alpha models. Standard SQL inner/outer joins on symbol and date silently join updated or restated feature values (e.g. restated EPS, post-revised GDP, late-arriving earnings releases) to historical timestamps, creating severe target leakage. This skill enforces as-of join semantics with strict availability lag constraints (`available_at <= timestamp`).

## Prerequisites

- Feature records with publication/availability timestamps `available_at`.
- Historical label records with feature query timestamps $T_{\text{query}}$.

## Workflow

1. **Format Feature & Label Tables**: Include `symbol`, `timestamp`, `available_at`, and `value`.
2. **Execute As-Of Join**: Join feature records to label timestamps enforcing $T_{\text{feature.available\_at}} \le T_{\text{label.timestamp}}$.
3. **Audit Data Availability Gap**: Verify no feature row has `available_at > timestamp`.
4. **Generate Point-In-Time Dataset**: Output clean training matrix ready for model fitting.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Revision Date Instead of Publication Date**: Joining economic indicators on event reference date rather than public release date.
- **Ignoring Vendor Ingestion Lags**: Assuming vendor API data was available immediately at market close without accounting for ingestion delay.

## Verification

- Join restated corporate earnings dataset with as-of query date before restatement, verify original value is returned.
- Run `python scripts/test_pit_ml_database.py` and confirm 100% pass rate.

## Related Skills

- `feature-engineering-without-leakage`
- `backtest-database-schema-for-point-in-time-queries`
- `feature-store-for-live-and-backtest-parity`
---
