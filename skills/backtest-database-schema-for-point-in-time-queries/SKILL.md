---
name: backtest-database-schema-for-point-in-time-queries
description: Use when designing a database schema that natively supports bitemporal
  point-in-time as-of queries, so lookahead-bias mistakes become structurally harder
  to introduce in backtests.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- point-in-time
- database-schema
- lookahead-bias
- temporal-queries
brokers_frameworks:
- Point-in-Time Schema Engine
- Python
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building data infrastructure for backtesting. Standard database tables return the latest value for a query, silently introducing lookahead bias. A point-in-time (PIT) schema stores records on two independent time axes, so queries like "what was the P/E ratio of AAPL as known on 2023-01-15?" return only data available at that historical moment.

The two axes follow the SQL:2011 temporal vocabulary:

| Axis | Column | Meaning |
|---|---|---|
| Knowledge time | `known_at` | When the value became **externally known** (vendor publication / filing release). Not the DB insert time. |
| Valid (application) time | `valid_from` | When the value **came into effect** in the real world (e.g. fiscal period end). |

## When NOT to Use

- **Market price bars** already carry their own event timestamp and are not restated; a PIT knowledge axis adds cost without benefit. Use ordinary time-series storage.
- **Live trading reads of current state** — as-of machinery is for reconstructing the past. Query the latest row directly.
- Four sibling skills cover adjacent ground; pick by what you are building:
  - designing the **schema and as-of query layer** itself → this skill;
  - joining **fundamentals** on SEC filing dates → `point-in-time-fundamentals-data-joins`;
  - assembling an **ML feature/label training matrix** → `point-in-time-database-for-ml-training-data`;
  - tracking **index membership** over time → `point-in-time-index-constituent-tracking`.

## Prerequisites

- Database or data store with temporal versioning capability.
- Historical fundamental/reference data with publication timestamps.
- A single documented timestamp convention (see `references/standards.md`) agreed across every ingesting vendor feed.

## Workflow

1. **Design PIT Schema**: Add `known_at` (knowledge time) and `valid_from` (valid time) to every fact table, plus a `revision` counter so simultaneous corrections resolve deterministically. Make the table append-only.
2. **Normalize Every Timestamp On Ingest**: Store one canonical representation — UTC, zero-padded, fixed-width ISO 8601. Reject anything else at the boundary rather than storing it and comparing later.
3. **Query with As-Of Semantics**: Filter `known_at <= as_of` **and** `valid_from <= as_of`. Filtering only on the knowledge axis still returns values that had been announced but were not yet in effect.
4. **Decide the Same-Day Rule Explicitly**: If a record's `known_at` has date granularity only, you cannot know whether it landed before or after the close. Treat it as known at end of day — so it is *not* tradable that same day — or store the real intraday timestamp.
5. **Validate No Future Leakage**: Audit that a naive "latest row wins" query and the PIT query return the same value; where they differ, the naive path is the bug.
6. **Index for Performance**: Create a composite index on `(symbol, field, known_at)`. Equality predicates must lead so the range predicate on `known_at` can bound the scan; omitting `field` leaves it as a post-filter.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using `created_at` Instead of `known_at`**: Database insertion time != when data was publicly available. A backfill loaded today would make every historical row look known today.
- **Restated Earnings Without Versioning**: Overwriting Q1 earnings with restated figures without preserving the original destroys the only record of what you could actually have traded on.
- **Comparing Timestamps As Strings**: Lexicographic comparison is chronologically correct only when every value shares an identical UTC offset representation and fractional-second precision (RFC 3339 §5.1). `2023-02-01T09:00:00-05:00` string-compares as earlier than `2023-02-01T12:00:00Z` but is actually two hours later — a silent leak.
- **Unpadded or Mixed-Format Dates**: A single `2023-9-01` sorts after `2023-10-01`, so the row becomes invisible to every as-of query that should have returned it. Validate the format on write, not on read.
- **Filtering Only the Knowledge Axis**: Guidance announced in January for a period ending in June is *known* in February but not yet *in effect*; returning it as February's value is a bitemporal modelling error.
- **Treating a Publication Date as Midnight**: An earnings release timestamped only `2023-06-01` is usually after the close. Assuming start-of-day makes it tradable a full session early.

## Verification

- Run `python scripts/test_pit_schema.py` — 100% pass rate.
- Insert an original figure and a later restatement sharing one `valid_from`; assert an as-of query before the restatement returns the original value, and after it returns the restated one.
- Assert that a record whose `known_at` carries a UTC offset is excluded when its UTC instant falls after the as-of cutoff, even though raw string comparison would admit it.

## Related Skills

- `point-in-time-fundamentals-data-joins`
- `point-in-time-database-for-ml-training-data`
- `point-in-time-index-constituent-tracking`
- `backtest-look-ahead-in-universe-selection`
- `backtest-determinism-and-reproducibility`
---
