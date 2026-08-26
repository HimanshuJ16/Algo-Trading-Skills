# Workflow — multi-timeframe-backtest-consistency-checks

## Preconditions

The check is a **comparison between two independent provenances of the same timeframe**.
Before starting, confirm you have:

- High-resolution bars, strictly time-ordered, of one known interval.
- A low-resolution series obtained **independently** of the resampling code under test.
- The venue's session start time in UTC.

If the reference series was produced by the same resampling code, stop — agreement would
prove only that the code is deterministic.

## Procedure

### 1. Compute the anchor arithmetic

Express the session open as seconds after UTC midnight and divide by the target bucket
width. A whole result means `ANCHOR_EPOCH` and `ANCHOR_SESSION` agree; a fractional result
means the epoch grid cuts the session's first bucket short, and you must decide which
convention the reference series uses.

```
session_offset = session_open_utc_seconds_past_midnight
aligned = (session_offset % (bar_interval_seconds * factor)) == 0
```

See `references/standards.md` for the worked NSE example.

### 2. Resample by wall-clock time

```python
result = checker.resample_bars(
    high_res_bars,
    factor=5,
    bar_interval_seconds=60,
    anchor=ANCHOR_EPOCH,          # from step 1
    drop_incomplete_final=True,   # never False for a backtest
)
```

Inspect before proceeding:

- `result.incomplete_buckets` — non-zero means source bars are missing inside buckets.
  Their high, low, and volume are computed over partial data and will legitimately differ
  from a complete reference. **Fix the source gap; do not widen the tolerance.**
- `result.dropped_incomplete_final` — true means the last period had not finished. Expected
  on a live feed; on a historical extract it means the download was truncated mid-bucket.

### 3. Verify aggregation exactly

```python
integrity = checker.check_resampling_integrity(
    high_res_bars, reference_low_res_bars,
    factor=5, bar_interval_seconds=60, anchor=ANCHOR_EPOCH,
)
```

Classify any failure from `integrity.field_mismatches`:

| Pattern | Likely cause | Next step |
|---|---|---|
| `volume` only | Double-counting, or lots vs shares | Check the vendor's volume unit and any pre-aggregation |
| `open`, `low`, `close` together | Boundary-anchor disagreement | Re-run with the other anchor before debugging aggregation |
| `high`/`low` only | Reference excludes trade conditions this series includes | Compare the vendor's tick-filtering rules |
| Large `missing_in_reference` / `missing_in_resampled` | Series cover different buckets, or different anchors entirely | Compare the first and last timestamps on both sides |

### 4. Verify indicator parity

```python
report = checker.check_consistency(
    high_res_bars, reference_low_res_bars,
    factor=5, sma_period=3, bar_interval_seconds=60, anchor=ANCHOR_EPOCH,
)
```

`sma_period` is in **low-resolution bars** and is applied unchanged to both series. Do not
scale it by `factor` — that compares two different estimators, which disagree on every
non-flat series regardless of resampling correctness.

`InsufficientDataError` means the check found nothing to compare. It is not a pass and must
not be caught and ignored. Supply at least `(sma_period + 1) * factor` high-resolution bars,
and confirm both series use the same anchor.

### 5. Interpret the result

- `max_divergence_pct` is relative to the reference, so the same absolute error reads ten
  times larger on a \$10 instrument than on a \$100 one. Read `max_absolute_divergence`
  alongside it.
- `worst_timestamp` locates the offending bucket — start the investigation there.
- A pass at the default tolerance means the two series agree to floating-point noise. A pass
  only after loosening the tolerance means you have an unexplained difference; record the
  mechanism before relying on either series.

## Reference

- `scripts/timeframe_consistency.py` — implementation
- `scripts/test_timeframe_consistency.py` — regression tests for each documented defect
- `references/standards.md` — threshold rationale, anchor arithmetic, version 1.0.0 defect record
- `assets/checklist.md` — printable pre-flight checklist
