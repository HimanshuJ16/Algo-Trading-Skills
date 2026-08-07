---
name: backtesting-alt-data-strategies-with-realistic-availability-lag
description: Use when backtesting trading strategies driven by alternative data (e.g.,
  credit card receipts, satellite imagery) to mathematically enforce Point-in-Time
  (PIT) publication lags and prevent catastrophic lookahead bias.
domain: algorithmic-trading
subdomain: data-management
tags:
- alternative-data
- lookahead-bias
- point-in-time
- backtesting
- publication-lag
brokers_frameworks:
- Pandas
- Point-in-Time Enforcement
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Alternative data (e.g., foot traffic, weather, credit card data) is almost never available on the day the event occurs. There is a processing, aggregation, and publication delay. Backtesting using the `event_date` instead of the `publication_date` creates massive Lookahead Bias, resulting in impossible simulated returns. Invoke this skill to wrap alternative datasets in a Point-in-Time (PIT) lag enforcer before feeding them to the backtest engine.

## Prerequisites

- Alternative data time-series loaded into a Pandas DataFrame.
- A known `publication_date` for each record, OR a known `default_lag_days` (e.g., 3 days for credit card data).
- The backtest engine must request data `as_of` the current simulated trading date.

## Workflow

1. **Initialize Enforcer**: Wrap the alternative dataset in `AltDataLagEnforcer`.
2. **Define Lags**: Specify `event_date_col`, `publication_date_col` (if available), and a fallback `default_lag_days`.
3. **Simulate Point-in-Time**: During the backtest loop, query the enforcer using `get_point_in_time_data(as_of_date)`.
4. **Enforce Causality**: The enforcer filters out any data where `publication_date > as_of_date`, guaranteeing the strategy cannot peek into the future.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Event Date as Trade Date**: E.g., buying a retail stock on Black Friday because the "Black Friday Sales" alt-data row was exceptionally high. That data isn't published until Wednesday.
- **Ignoring Revision Bias**: Data vendors often restate historical data. If the dataset does not have versioned rows (Knowledge Time vs Valid Time), the backtest is using the revised "perfect" data rather than the noisy initial estimate.

## Verification

- Query the enforcer for `as_of_date = 2023-11-25`. An event that occurred on `2023-11-24` with a 3-day lag should NOT be returned.
- Run `python scripts/test_alt_data_lag_enforcer.py` and confirm 100% pass rate.

## Related Skills

- `backtest-look-ahead-in-universe-selection`
- `point-in-time-fundamentals-data-joins`
