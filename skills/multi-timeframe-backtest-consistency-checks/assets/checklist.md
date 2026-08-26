# Checklist — multi-timeframe-backtest-consistency-checks

## Provenance
- [ ] The reference low-resolution series is **independent** of the resampling code under test
- [ ] Both series cover the same period, instrument, and session

## Boundary anchor
- [ ] Session open expressed as seconds past UTC midnight
- [ ] `session_offset % (bar_interval_seconds * factor)` computed; anchor chosen from the result
- [ ] Anchor convention of the reference series confirmed, not assumed

## Input integrity
- [ ] Timestamps strictly increasing; no duplicates
- [ ] Every gap is a whole multiple of the declared `bar_interval_seconds`
- [ ] Non-finite values and OHLC relation violations filtered upstream

## Resampling
- [ ] Bucketing is by wall-clock time, never by list position
- [ ] `incomplete_buckets` inspected; any non-zero count traced to a source gap and fixed
- [ ] `drop_incomplete_final=True` for every backtest run
- [ ] `dropped_incomplete_final` reviewed — on a historical extract it means a truncated download

## Verification
- [ ] `check_resampling_integrity()` run first; `field_mismatches` classified per `references/workflows.md`
- [ ] `check_consistency()` run with `sma_period` in low-resolution bars, **not** scaled by `factor`
- [ ] `InsufficientDataError` treated as a failure, never caught and ignored
- [ ] `max_absolute_divergence` read alongside `max_divergence_pct`
- [ ] Any tolerance above the default has a recorded mechanism explaining the difference
- [ ] Tests pass: `python scripts/test_timeframe_consistency.py`

## Sign-off
- Reviewed by: ___________________________
- Date: ___________________________
