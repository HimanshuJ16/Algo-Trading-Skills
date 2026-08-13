# Standards — backtesting-alt-data-strategies-with-realistic-availability-lag

## The Two Time Axes

| Axis | Field | Meaning |
|---|---|---|
| Valid (application) time | `event_date_col` | When the real-world event happened. |
| Knowledge time | effective publication date | When the row became usable by a strategy. |

Terminology matches `backtest-database-schema-for-point-in-time-queries`, which uses the
SQL:2011 vocabulary. A point-in-time query admits a row only when its knowledge time is at
or before the query instant. The knowledge time is the vendor's publication timestamp
where one exists, and `event_date + default_lag_days` where one does not.

## Constraints the Enforcer Actually Applies

| Constraint | Rule | Enforced |
|---|---|---|
| Fallback lag | `default_lag_days >= 0` | Raises on construction |
| Publication vs event | Publication date may not precede its own event date | Raises on construction |
| Named publication column | Must exist in the frame | Raises on construction |
| Timezone | Dataset and `as_of` must agree on awareness | Raises on query |
| Revision keys | Must exist in the frame | Raises on construction |
| Lag calendar | `"calendar"` or `"business"` | Raises on construction |

A previous version of this file asserted a **"Minimum Alt Data Lag: must be ≥ 1 day"**
rule. That was neither enforced by the code nor universally true — vendor materials
describe products delivering on a T+1 and even same-day basis. The enforced floor is
`>= 0`; a zero lag is permitted and is occasionally correct, but it means the data was
usable the instant the event occurred, which is worth justifying in writing.

## Lag Conventions

**Calendar days are the default**, matching the T+N convention vendors quote. Set
`lag_calendar="business"` only where the vendor's pipeline is known to run on business
days.

Business-day handling uses `numpy.busday_offset` with `roll="forward"`:

- An event on a non-business day is rolled **forward** to the next business day, then the
  lag is counted. Saturday 2023-11-25 with a 3-business-day lag becomes available on
  Thursday 2023-11-30.
- This deliberately differs from `pandas.offsets.CustomBusinessDay`, which rolls backward
  and would publish on Wednesday 2023-11-29 — a day earlier. Where two readings are
  defensible, a look-ahead guard takes the later one.
- Weekends are excluded automatically; **holidays are not**, unless passed via
  `holidays=`. `pandas.tseries.offsets.CustomBusinessDay` documents `holidays` and
  `calendar` as explicit parameters for exactly this reason — the plain `BusinessDay`
  offset has no holiday awareness. A Thanksgiving or Christmas in the lag window will
  otherwise be counted as a working day.

## Date Granularity and Delivery Time

A publication date stored as a bare date parses to midnight, which asserts the file
existed at 00:00 that day. `availability_time` pins midnight stamps to the hour the
vendor actually delivers; stamps that already carry a time-of-day are left untouched,
since that is real vendor precision rather than a date.

The default is `None` — midnight, the permissive reading — to preserve prior behaviour.
Setting it is strongly recommended when the backtest makes intraday decisions. Where the
delivery hour is unknown, `datetime.time(23, 59, 59)` is the conservative choice and
matches the same-day rule used in `backtest-database-schema-for-point-in-time-queries`.

## Revisions

Vendors restate. Where the dataset carries versioned rows, set `revision_key_cols` to the
columns identifying one logical observation, and a query returns only the latest revision
published on or before the query date. Ties on publication timestamp resolve to the later
row in the source frame, deterministically.

Deduplication is **opt-in**, because a dataset legitimately carrying many rows per
event date — one per merchant, region, or category — would be destroyed by keying on the
date alone. When no key is set and duplicate event dates are present, construction logs a
warning and `lag_audit()["duplicate_event_rows_without_key"]` reports the count.

Note the limit: this handles restatements the vendor *ships as new rows*. If the vendor
overwrites history in place, nothing in the dataset records what was previously believed,
and only stored daily snapshots recover true point-in-time behaviour.

## Auditing the Guarantee

`lag_audit()` separates evidence from assumption:

- `explicit_publication_rows` — rows resting on a real vendor publication timestamp.
- `fallback_rows` — rows resting on `default_lag_days`. A high count means the PIT
  guarantee is an assumption. Track it; it is the difference between a measured lag and a
  guessed one.
- `duplicate_event_rows_without_key`, `lag_calendar`, `availability_time` — the
  configuration the numbers were produced under.

`get_point_in_time_data(..., include_effective_date=True)` returns the knowledge-time
column as `effective_publication_date`, so the admission of every row is traceable.

## Vendor Lag Figures — Read With Care

Published alternative-data lags vary by product and vendor. Figures encountered while
preparing this file include T+1 for email-receipt data, roughly T+3 to T+7 for card
panels, and T+4 to T+6 for merchant-level card data.

**Sourcing:** these come from vendor and data-marketplace marketing pages, not from
contracts, filings, or a standards body. They are indicative only and should not be used
as defaults. **The only authoritative lag for your backtest is the one in your own
delivery contract**, and it should be confirmed against observed file arrival times
rather than taken from a datasheet.

## Scope Boundary

This enforcer applies a knowledge-time filter to a supplied frame. It does not source,
license, or validate the data; detect panel-composition or methodology drift; recover
history a vendor overwrote; verify that the publication timestamps you supplied are
truthful; or determine whether trading on the dataset is permitted. Those belong to
`alternative-data-vendor-due-diligence-checklist` and
`insider-trading-controls-for-alternative-data-usage`.

## Sources

- pandas, `pandas.tseries.offsets.CustomBusinessDay` — `weekmask`, `holidays` and
  `calendar` parameters; standard `BusinessDay` has no holiday support —
  <https://pandas.pydata.org/docs/reference/api/pandas.tseries.offsets.CustomBusinessDay.html>
- `numpy.busday_offset` `roll` semantics, used for the business-day path.
- Vendor and marketplace materials for the indicative lag figures above (secondary; see
  the caveat in that section).

## Category

`data-management` — see top-level `mappings/` directory.
