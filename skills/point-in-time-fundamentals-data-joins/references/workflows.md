# Workflows for Point-in-Time Fundamentals Data Joins

Deep procedure for `PointInTimeFundamentalsDataJoinsEngine`. The short form is in
`SKILL.md`; this file is the implementation reference.

## 0. Build a versioned filing store first

A PIT join is impossible on a table that edits rows in place. Before anything
below, confirm the source is **append-only per filing**:

| Bad (unusable) | Good (PIT-capable) |
|---|---|
| One row per `(ticker, period_end_date)`, updated when a restatement lands | One row per `(ticker, metric, period_end_date, filing_date, revision_number)` |
| "EPS for FY2022 is 1.20" | "EPS for FY2022 was reported 1.50 on 2023-02-15 and restated to 1.20 on 2023-08-10" |

If the vendor only ships the current view, the history has to be rebuilt from
filing-by-filing snapshots or from EDGAR itself. There is no way to recover it
after the fact from a single overwritten table — the original number is gone.

## 1. Ingest and validate

`insert_filings(records)` validates every record and stores none of the batch if
any record fails. Rejections, each raising `ValueError`:

- `period_end_date`, `filing_date`, `non_reliance_date` not strict `YYYY-MM-DD`,
  or not a real calendar date (`2023-02-30`).
- `filing_date < period_end_date` — a filing cannot report an unfinished period.
- `non_reliance_date < filing_date` — a figure cannot be disclaimed before it exists.
- `value` non-finite (`NaN`, `±Inf`) or not a real number (strings, `None`, `bool`).
- `revision_number` negative or not an `int`.
- blank `ticker` or `metric_name`.

The strict date pattern is enforced *before* `date.fromisoformat`, because from
Python 3.11 that function also accepts layouts such as `'20230215'`. Pinning the
pattern keeps behaviour identical across interpreter versions.

## 2. Compute availability

```
availability_date = filing_date + availability_lag_days   # calendar days
```

`availability_lag_days` comes from `Config.default_availability_lag_days`,
default `1`.

Calendar days are correct for `<=` comparison against trading dates: a Friday
filing becomes available Saturday, which is still `<=` the following Monday, so
no trading day is skipped. It is *not* a business-day calculation and must not be
reused as one.

Choosing the lag:

| Situation | Lag |
|---|---|
| Vendor supplies EDGAR's assigned filing date only | `1` (default) |
| Vendor supplies EDGAR `ACCEPTANCE-DATETIME` and you have already resolved intraday availability | `0` |
| Vendor ingests and publishes on a known delay after the filing | `1 + vendor delay in days` |
| Backtest trades the *next* open rather than the same close | `1` is already sufficient; do not double-count the lag in the strategy as well |

## 3. Filter to what was public at `T`

```
valid  = [r for r in matching if r.period_end <= T and r.availability_date <= T]
naive  = [r for r in matching if r.period_end <= T]
```

`matching` is the ticker/metric set, case- and whitespace-insensitive, optionally
narrowed to one `period_end_date` when `PITQuery.period_end_date` is set.

The `naive` set is not used to answer the query. It exists solely to compute the
counterfactual in step 5.

## 4. Select the record

Order descending by `(period_end_date, filing_date, revision_number, value)` and
take the first.

The `value` component is a deterministic tiebreak only. If two records tie on the
first three components with different values, the source data is corrupt: the
engine still answers deterministically, sets `ambiguous_candidate_count > 1`, and
writes a `WARNING` into `audit_notes`. Treat that as a data-quality alert, not a
resolved condition.

**Why period end is the primary key.** Consider:

| Period end | Filing date | Rev | Value |
|---|---|---|---|
| 2022-12-31 | 2023-02-15 | 0 | 1.50 |
| 2023-03-31 | 2023-04-20 | 0 | 1.80 |
| 2022-12-31 | 2023-08-10 | 1 | 1.20 |

At `T = 2023-09-01` all three are public. Sorting by filing date first selects the
third row — the FY-2022 figure, restated — and reports it as the latest known EPS,
five months after Q1-2023 was published. Sorting by period end first selects the
second row, 1.80 for period 2023-03-31, which is what an analyst at that date
would quote. To ask about FY-2022 specifically, set
`PITQuery(period_end_date='2022-12-31')`, which returns 1.20 as restated — also
correct, because by 2023-09-01 the restatement was public.

## 5. Audit the naive counterfactual

Run step 4 again over `naive` to get `naive_record`, then classify:

```
unreleased  = naive_record is not None
              and (best is None or naive_record.period_end > best.period_end)

restatement = best is not None
              and any(r.period_end == best.period_end and key(r) > key(best)
                      for r in naive)
```

| `unreleased` | `restatement` | `leakage_type` |
|---|---|---|
| False | False | `NONE` |
| True | False | `UNRELEASED_FILING` |
| False | True | `RESTATEMENT` |
| True | True | `UNRELEASED_AND_RESTATEMENT` |

The two flags answer different questions and imply different fixes:

- `UNRELEASED_FILING` — the naive join reached into a fiscal period whose report
  was not yet filed. The fix is in the join predicate.
- `RESTATEMENT` — the naive join would have overwritten the as-reported figure
  for the *matched* period with a later revision. The fix is in the data store's
  versioning.

Reporting the first as the second is the classic failure of a count-based audit
(`len(naive) > len(valid)`), which fires on any filtered record and therefore
claims blocked restatement leakage on data containing no restatements at all.

`naive_join_value`, `naive_join_period_end_date` and `naive_join_filing_date`
carry the counterfactual itself, so the leakage has a magnitude. Sweeping a
universe and summing `|pit_value - naive_value|` is the practical way to size how
much of a strategy's historical edge was lookahead.

When `best is None` the query returns `NO_DATA_AVAILABLE_AS_OF_DATE`. There is no
matched period, so `restatement_leakage_blocked` is `False` by construction and
the classification reports `UNRELEASED_FILING` — the honest description of "the
data exists in the table but was not public yet".

## 6. Flag non-reliance

If the matched record has `non_reliance_date <= T`, set `is_non_reliance_flagged`
and append a `NON-RELIANCE` note. The value is still returned.

The reasoning: an Item 4.02 Form 8-K lands *before* the corrected filing, often by
months. In that interval the market knows the figure is unreliable and typically
reprices the name, but no corrected figure exists. Returning nothing would put a
value in the backtest that no one had; returning the figure unflagged would trade
a known-bad input. Both are decisions the caller must make explicitly — usually
by suppressing the signal for that name until the amendment lands.

## 7. Consume the report

```python
report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-05-01"))
if report.status != "RECORD_FOUND_PIT_VALID":
    ...                                   # no fundamental for this bar
elif report.is_non_reliance_flagged:
    ...                                   # suppress the signal for this name
else:
    signal = f(report.matched_record_value)
```

Never branch on `audit_notes`. It is a human- and log-facing string; the machine
contract is `status`, `leakage_type`, and the typed fields.

## 8. Sweep the universe before trusting a backtest

1. For every `(ticker, bar_date)` in the backtest, run the join.
2. Aggregate `leakage_type` counts.
3. Aggregate `|matched_record_value - naive_join_value|` where both are present.
4. Compare the strategy's PnL under both joins.

A strategy whose edge collapses under the PIT join was trading the difference
between the two, which is to say it was trading the future.
