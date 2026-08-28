---
name: point-in-time-index-constituent-tracking
description: >-
  Use when a backtest needs the index members that were actually in an index on a
  historical date — resolves an addition/deletion event log into the half-open
  membership interval [add_date, del_date) so delisted and removed names stay in the
  historical universe. Resolves the effective-date axis only; it does not model
  announcement timing, delisting settlement prices, or index weights.
domain: Data Management Global
subdomain: Index Membership & Backtest Parity Architecture
tags: ["pit", "point-in-time", "index-constituents", "survivorship-bias", "sp500", "backtest-parity", "corporate-actions"]
brokers_frameworks: ["S&P Dow Jones Indices", "MSCI", "FTSE Russell", "CRSP", "Python standard library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building equity universe selectors for quantitative backtesting. Applying today's static index constituents to historical backtest dates introduces survivorship bias: names that were removed after a bankruptcy, merger, or delisting disappear from the historical universe, and names added later appear in it before they were ever members. This engine reconstructs the membership set for any historical date from an addition/deletion event log.

Membership moves on the **effective** axis, and one convention governs every query. S&P Dow Jones Indices makes constituent changes "effective prior to the open of trading" on the effective date, so membership is the half-open interval `[add_date, del_date)`:

| As-of date `T` relative to the event | Member on `T`? |
|---|---|
| `T` equals the addition's effective date | Yes — it trades in the index for that whole session |
| `T` equals the deletion's effective date | No — it was already out before the open |
| `T` between them | Yes |

The magnitude of the bias this removes is not a single number: published estimates for equity strategies vary by hundreds of basis points per year depending on the index, the rebalancing frequency, and which specific names were removed. Treat any headline figure as strategy-specific rather than a constant, and measure it on your own universe with the ghost audit below.

## When NOT to Use

- **Not an announcement-timing auditor.** This resolves *when membership took effect*, not *when the change became knowable*. Scheduled index changes are announced days to weeks before they take effect (Tesla's S&P 500 addition was announced 2020-11-16 and effective prior to the open on 2020-12-21). A strategy that must not act on an unannounced change needs the knowledge axis too — use `backtest-look-ahead-in-universe-selection`.
- **Not a delisting-settlement model.** Knowing a name left the index on date `D` is not the same as settling its terminal P&L. Bankruptcy write-offs and merger consideration are handled by `survivorship-bias-free-universe-construction`.
- **Not a source of point-in-time index weights.** `constituent_weights` reports the weight carried on each member's most recent membership-affecting event. Weights drift with price and are reset at rebalances that emit no addition or deletion, so this is provenance metadata, not a portfolio weight.
- **Not a substitute for point-in-time source data.** A clean resolution means the event log you supplied is internally consistent. If the log was reverse-engineered from a current-membership table, every query still returns a survivorship-biased universe.
- **Not for live universe construction.** As-of machinery reconstructs the past; a live rebalance should read current membership directly.

## Prerequisites

- A constituent event log with `index_name`, `symbol`, `event_type` (`'ADDITION'` / `'DELETION'`) and `effective_date`, as strict zero-padded `YYYY-MM-DD` strings or `datetime.date` objects.
- Whether your vendor stores the deletion date as the **first non-member day** (half-open, what this engine expects) or as the **last day of membership** (inclusive). If inclusive, add one session before ingesting, or every name will appear to leave a day early.
- A stable `security_id` (CUSIP, SEDOL, CRSP PERMNO, FIGI) wherever available. Membership is keyed by ticker when no `security_id` is supplied.
- Optionally, today's membership as a set, to run the ghost audit.

## Workflow

1. **Normalise the event log before ingest**: Convert dates to zero-padded ISO-8601 and reconcile the vendor's deletion-date convention to half-open. `insert_events` rejects non-ISO dates rather than accepting them — string date comparison is date comparison only for zero-padded ISO-8601, and `'2020-1-5'` sorts before `'2020-01-06'` would not.
2. **Key membership by security, not by ticker**: Populate `security_id`. Tickers are reused across issuers — the `GM` ticker was reassigned to the new General Motors Company after the old General Motors' shares became `GMGMQ` and then `MTLQQ` in 2009. Keyed by ticker alone, two different companies collapse into one membership timeline and one of them silently inherits the other's dates.
3. **Resolve the as-of universe**: Call `query_pit_universe`. A security is a member when its latest event at or before `T` is an `ADDITION`.
4. **Check `status` before using the result**: `INDEX_NOT_FOUND` means no events were ever ingested for that index name — an empty universe from a typo'd index name, not an empty index. Never feed an empty `active_constituents` into a backtest without distinguishing the two.
5. **Read `data_quality_warnings`**: An addition and a deletion sharing one effective date for one security is a feed anomaly. The engine resolves it deterministically (deletion first, so a same-day delete/re-add ends as a member) and always reports it. Supply `sequence` on the events to make the feed's own ordering authoritative instead.
6. **Run the survivorship-bias ghost audit**: Pass `current_static_universe` to count PIT members missing from today's index. Without it, `survivorship_bias_ghost_count` is `None`, which means *not audited* — never read it as zero.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Today's Index Constituents**: Backtesting a 2015 strategy against current index membership, so every name that later went bankrupt or was acquired is missing and every later addition is present from the start.
- **Inclusive Deletion Dates**: A vendor that stores `del_date` as the *last* day of membership, fed into a half-open engine, removes every name one session early. This is silent — the universe simply gets slightly smaller.
- **Reading a `None` Ghost Count as Zero**: `survivorship_bias_ghost_count is None` means the audit did not run because no current universe was supplied. An agent that reports "0 ghosts, no survivorship bias" from an unaudited query has inverted the finding.
- **Trusting an Empty Universe**: A misspelled `index_name` returns an empty membership set. Gate on `status == 'UNIVERSE_RESOLVED_PIT'`, not on the constituent list being non-empty.
- **Non-Padded Dates**: `'2020-1-5'` compares lexicographically *after* `'2020-12-31'`. Any engine that compares raw date strings silently reorders the whole event log; this one rejects them at ingest.
- **Symbol Re-Use Errors**: Confusing historical tickers that were reassigned to a different company. Without a `security_id` the engine cannot tell them apart, and the resulting universe attributes one issuer's membership window to another.
- **Mistaking Effective Dates for Announcement Dates**: Trading on membership the day it becomes effective is correct; trading on it the day it was *announced* is not, and this engine cannot tell you the difference.
- **Ignoring Delisting Exit Prices**: Including a delisted name in the universe without modelling its terminal value (zero for a bankruptcy write-off) replaces survivorship bias with a different error.

## Verification

- Ingest an addition of `ENE` effective 1990-01-01 and its deletion, plus an addition of `TSLA` effective 2020-12-21. Query as of 2000-06-01 and verify `ENE` is present and `TSLA` absent; query as of 2023-01-01 and verify the reverse.
- Assert the interval is half-open: a name whose addition is effective on `T` is a member on `T`, and a name whose deletion is effective on `T` is not.
- Assert that ingesting the same events in reverse order resolves to an identical universe.
- Assert that `'2020-1-5'`, `'01/05/2020'`, and an `event_type` of `'ADD'` each raise `IndexConstituentError` at ingest rather than resolving to a quietly different universe.
- Assert that a query with no `current_static_universe` reports `survivorship_bias_ghost_count is None`, and that an unknown index reports `status == 'INDEX_NOT_FOUND'`.
- Run `python -m unittest discover -s skills/point-in-time-index-constituent-tracking/scripts` and confirm 100% pass rate.

## Related Skills

- `backtest-look-ahead-in-universe-selection`
- `survivorship-bias-free-universe-construction`
- `point-in-time-fundamentals-data-joins`
- `reference-data-symbol-mapping-across-vendors`
---
