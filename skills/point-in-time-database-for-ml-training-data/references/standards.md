# Standards — point-in-time-database-for-ml-training-data

## Category

`financial-ml`

## Join Semantics

| Requirement | Rule | Enforcement |
|---|---|---|
| Temporal availability | `available_at <= label_timestamp` | Non-strict (inclusive) inequality on the **knowledge** axis, evaluated on timezone-aware UTC instants |
| Event axis | `event_timestamp` never gates the join | Audit-only, unless `require_event_before_label=True` |
| Revision handling | Latest value knowable at the label instant wins; restatements stay invisible until published | Total order `(available_at, revision, insertion_sequence)` |
| Staleness | A match older than `max_staleness_days` is refused, not carried forward | `is_stale=True`, `is_valid_pit=False`, `feature_value=None` |
| Missing data | Unknowable cells stay `None` | Never imputed inside the join |
| Value domain | Feature values and targets must be finite | Non-finite input rejected at ingest with `ValueError` |

Note the inequality is **inclusive**: a value knowable at exactly the label instant is usable. The conservative behaviour for date-granular publication comes from resolving the *instant*, not from tightening the comparison.

## Timestamp Format

Lexicographic comparison of ISO 8601 strings is chronologically correct only under conditions this engine cannot assume across vendor feeds. RFC 3339 §5.1:

> "Assuming that the time zones of the dates and times are the same (e.g., all in UTC), expressed using the same string (e.g., all "Z" or all "+00:00"), and all times have the same number of fractional second digits, then the date and time strings may be sorted as strings […] and a time-ordered sequence will result."

Accordingly every timestamp is parsed to a timezone-aware UTC `datetime` at the boundary. Unpadded components (`2023-9-01`) are rejected rather than stored, because they sort *after* `2023-10-01` and turn a present record into a silently missing one.

| Input form | Interpreted as | Granularity |
|---|---|---|
| `2023-01-20` / `20230120` / `datetime.date` | `2023-01-20T00:00:00Z` | date-only |
| `2023-01-20T16:00:00Z` | as given | instant |
| `2023-01-20T09:00:00-05:00` | `2023-01-20T14:00:00Z` | instant |
| naive `datetime` | same wall time, UTC | instant |
| `2023-9-01`, empty string, non-string | rejected (`ValueError`) | — |

## Date-Granular Availability Policy

| Policy | Feature `available_at` = `D` resolves to | Same-day use |
|---|---|---|
| `end_of_day` (default) | `D+1 00:00:00Z` | Blocked |
| `start_of_day` | `D 00:00:00Z` | Permitted |

A date-granular `label_timestamp` always resolves to the **start** of its day.

## Reference Points for Publication Lag

These are context for why `event_timestamp != available_at`, not thresholds the engine enforces. Source the real `available_at` from the vendor; never synthesise it from these figures and then describe the result as point-in-time correct.

| Source | Event axis | Knowledge axis | Typical lag |
|---|---|---|---|
| SEC Form 10-K | Fiscal year end | EDGAR filing acceptance | 60 / 75 / 90 days for large accelerated / accelerated / non-accelerated filers |
| SEC Form 10-Q | Fiscal quarter end | EDGAR filing acceptance | 40 / 40 / 45 days by the same filer categories |
| FRED / ALFRED macro series | Observation `date` | `realtime_start` (vintage date) | Varies by series; revised repeatedly |

ALFRED (ArchivaL FRED) is the reference implementation of this two-axis model for macro data: each observation carries `date`, `realtime_start` and `realtime_end`, where the real-time period defines the vintage dates for which that value was the latest revision available. `realtime_start` is the knowledge-axis analogue of `available_at`.

## Relationship to `pandas.merge_asof`

The join implemented here corresponds to `merge_asof(..., direction="backward", allow_exact_matches=True, tolerance=max_staleness)`, keyed on `available_at` rather than the event date. Two documented `merge_asof` properties matter if you implement this in pandas instead:

- Both frames **must be sorted by the merge key in ascending order** beforehand; `merge_asof` does not sort for you, and an unsorted right frame yields wrong matches, not an error.
- `direction="backward"` selects the *last* row at or before the key. Rows tied on the merge key therefore resolve by frame order, which is why an explicit `revision` column is required for deterministic restatement handling.

## Sources

- RFC 3339, *Date and Time on the Internet: Timestamps*, §5.1 "Ordering" — https://www.rfc-editor.org/rfc/rfc3339
- `pandas.merge_asof` API reference — https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html
- SEC Form 10-K / Form 10-Q General Instruction A.(1); deadlines set by Release No. 33-8644, *Revisions to Accelerated Filer Definition and Accelerated Deadlines for Filing Periodic Reports* — https://www.sec.gov/files/rules/final/33-8644.pdf
- ALFRED (ArchivaL Federal Reserve Economic Data), vintage / real-time period model — https://alfred.stlouisfed.org/
