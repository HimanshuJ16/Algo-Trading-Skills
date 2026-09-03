---
name: backtesting-alt-data-strategies-with-realistic-availability-lag
description: >-
  Use when backtesting an alternative data strategy, to key every observation on its
  publication date rather than its event date, because the gap between the two is where
  impossible backtest results come from.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: alternative-data, lookahead-bias, point-in-time, backtesting, publication-lag
  brokers_frameworks: "Pandas; Point-in-Time Enforcement"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Alternative data (e.g., foot traffic, weather, credit card data) is almost never available on the day the event occurs. There is a processing, aggregation, and publication delay. Backtesting using the `event_date` instead of the `publication_date` creates massive Lookahead Bias, resulting in impossible simulated returns. Invoke this skill to wrap alternative datasets in a Point-in-Time (PIT) lag enforcer before feeding them to the backtest engine.

The two axes follow the SQL:2011 vocabulary this repository uses in `backtest-database-schema-for-point-in-time-queries`: **valid time** is `event_date` (when the event happened), **knowledge time** is the effective publication date (when the row became usable). A backtest may only read rows whose knowledge time has already passed.

## When NOT to Use

- **Not a licensing or compliance control.** Enforcing a lag says nothing about whether you are permitted to trade on the dataset. Material non-public information, vendor contract restrictions, and consent provenance are separate questions — see `insider-trading-controls-for-alternative-data-usage` and `alternative-data-vendor-due-diligence-checklist`.
- **Not a substitute for vendor snapshots.** If the vendor overwrites history in place, no filter can recover what the file said last year. `revision_key_cols` only helps when the dataset already carries versioned rows. Without stored daily snapshots the backtest reads today's restated figures regardless.
- **Not an intraday engine.** The knowledge-time axis is a timestamp, so intraday precision works if the vendor supplies it, but the fallback lag is whole days and `availability_time` is a single delivery time per dataset. Sub-daily arrival jitter is out of scope.
- **Not a data-quality check.** It does not detect a vendor silently changing panel composition, coverage, or methodology mid-history, all of which distort a backtest as badly as look-ahead.
- **Not sufficient on its own.** A correct lag on a universe selected with hindsight still leaks. Pair with `backtest-look-ahead-in-universe-selection`.

## Prerequisites

- Alternative data time-series loaded into a Pandas DataFrame.
- A known `publication_date` for each record, OR a known `default_lag_days` taken from **your vendor contract** — published lags vary widely across vendors and products, so treat any figure quoted elsewhere as an illustration, not a default.
- The backtest engine must request data `as_of` the current simulated trading date.
- One timezone convention across the dataset and the `as_of` query; mixing naive and aware timestamps raises.
- For restatement handling, columns that identify one logical observation (e.g. `["ticker", "event_date"]`).

## Workflow

1. **Initialize Enforcer**: Wrap the alternative dataset in `AltDataLagEnforcer`. Construction validates the dataset and fails loudly rather than degrading the guarantee.
2. **Define Lags**: Specify `event_date_col`, `publication_date_col` (if available), and a fallback `default_lag_days`. Naming a `publication_date_col` that is not in the frame **raises** — it will not quietly substitute the fallback lag. Pass `publication_date_col=None` to choose the fallback deliberately.
3. **Choose the Lag Calendar**: `default_lag_days` is **calendar** days by default, matching the T+N convention vendors quote. Set `lag_calendar="business"` where the vendor's pipeline only runs on business days, and pass `holidays=` — business-day offsets skip weekends but not holidays.
4. **Decide the Delivery Time**: A bare date lands at midnight, which asserts the file existed at 00:00. Set `availability_time` to the hour the vendor actually delivers, or `time(23, 59, 59)` for the conservative reading, so a 09:30 decision cannot read an 18:00 file.
5. **Handle Restatements**: Set `revision_key_cols` so a query returns the version known on that date, rather than the original and every later revision together. Without it the enforcer logs a warning when duplicate event dates are present, and `lag_audit()["duplicate_event_rows_without_key"]` counts them.
6. **Simulate Point-in-Time**: During the backtest loop, query the enforcer using `get_point_in_time_data(as_of_date)`.
7. **Enforce Causality**: The enforcer filters out any data where `publication_date > as_of_date`, guaranteeing the strategy cannot peek into the future.
8. **Audit the Guarantee**: `lag_audit()` separates rows resting on a real vendor publication date from rows resting on the assumed lag. A high `fallback_rows` means the PIT guarantee is an assumption, not evidence. Pass `include_effective_date=True` to return the knowledge-time column so every admitted row is traceable.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Event Date as Trade Date**: E.g., buying a retail stock on Black Friday because the "Black Friday Sales" alt-data row was exceptionally high. That data isn't published until Wednesday.
- **Ignoring Revision Bias**: Data vendors often restate historical data. If the dataset does not have versioned rows (Knowledge Time vs Valid Time), the backtest is using the revised "perfect" data rather than the noisy initial estimate.
- **A Mistyped Publication Column Voiding the Guarantee**: The single most dangerous failure. An enforcer that falls back to `default_lag_days` when the named column is absent replaces real publication dates with a guess — a real 2024-06-01 publication date became visible on 2023-11-27, six months early.
- **Returning Every Revision at Once**: Without a revision key, a restated observation is returned twice — the original estimate and the revision — and any aggregation double-counts it.
- **Calendar Days Where the Vendor Means Business Days**: A Friday event with a "3 day" lag becomes visible Monday under calendar counting and Wednesday under business counting. Across every weekend that is two days of look-ahead.
- **Weekend Events Under a Business Calendar**: A Saturday event has no business-day pipeline start until Monday. Rolling *backward* to Friday before counting publishes a day early; this enforcer rolls forward.
- **Midnight Publication Timestamps**: A date-only stamp read as 00:00 gives a full extra session of access to a file that may not land until the evening.
- **Publication Before the Event**: A row whose publication date precedes its own event date is not a short lag — it is corrupt data claiming knowledge of an event that had not happened.
- **Negative Fallback Lags**: A negative `default_lag_days` exposes data before its event. Rejected on construction.
- **Timezone Drift**: Alt-data delivery times straddle midnight. A silent naive/aware mismatch shifts availability by a day; the enforcer raises instead.

## Verification

- Query the enforcer for `as_of_date = 2023-11-25`. An event that occurred on `2023-11-24` with a 3-day lag should NOT be returned.
- Name a `publication_date_col` that does not exist and assert construction raises rather than falling back.
- Feed a restated observation and assert that with `revision_key_cols` set, a query before the restatement returns the original value and a query after it returns the revision — exactly one row either way.
- Assert Friday `2023-11-24` with a 3-business-day lag is invisible on Monday and visible on Wednesday `2023-11-29`; assert Saturday `2023-11-25` is visible only on Thursday `2023-11-30`.
- Assert that with `availability_time=time(18, 0)`, a `09:30` query on the publication date returns nothing.
- Sweep a simulated backtest loop and assert `max(effective_publication_date) <= as_of` on every day.
- Run `python -m unittest discover -s skills/backtesting-alt-data-strategies-with-realistic-availability-lag/scripts` and confirm 100% pass rate.

## Related Skills

- `backtest-look-ahead-in-universe-selection`
- `point-in-time-fundamentals-data-joins`
- `backtest-database-schema-for-point-in-time-queries`
- `alternative-data-vendor-due-diligence-checklist`
- `insider-trading-controls-for-alternative-data-usage`
