# Deep Workflow Reference — backtesting-alt-data-strategies-with-realistic-availability-lag

This file holds the full technical procedure referenced by `SKILL.md`. Conventions,
sourcing and the scope boundary live in `references/standards.md`.

## Full Procedure

1. **Establish the real lag before writing any code.**
   Read the delivery contract, then confirm it against observed file arrival times. Record
   three things: the lag length, whether it is quoted in calendar or business days, and
   the time of day files land. All three change results, and only the first is usually
   documented.

2. **Ingest with both dates.**
   Load the vendor data with the date the event actually happened (`event_date`) and, where
   the vendor supplies it, the timestamp the file was physically made available for
   download (`publication_date`). Prefer a real timestamp over any lag assumption: the
   assumption is what `lag_audit()` will flag as unproven.

3. **Construct the enforcer.**
   ```python
   enforcer = AltDataLagEnforcer(
       df,
       event_date_col="event_date",
       publication_date_col="pub_date",          # omit deliberately, never by accident
       default_lag_days=4,                       # from the contract
       lag_calendar="business",                  # if the vendor pipeline skips weekends
       holidays=["2023-11-23", "2023-12-25"],    # business calendars ignore these otherwise
       availability_time=dt.time(18, 0),         # when files actually land
       revision_key_cols=["ticker", "event_date"],
   )
   ```
   Construction validates and raises. A `publication_date_col` that is not in the frame is
   an error, not a cue to fall back — that substitution is the failure mode this class
   exists to prevent.

4. **Check the audit before trusting a single query.**
   ```python
   audit = enforcer.lag_audit()
   ```
   If `fallback_rows` is a large share of `total_rows`, the point-in-time guarantee rests
   on an assumed lag rather than on vendor evidence, and the backtest's credibility is
   bounded by that assumption. If `duplicate_event_rows_without_key` is non-zero and no
   revision key is set, restatements will be returned more than once.

5. **Query inside the backtest loop.**
   ```python
   for as_of in trading_days:
       visible = enforcer.get_point_in_time_data(as_of)
       signal = build_signal(visible)
       # execute at the NEXT open, not this one
   ```
   Signals derived from data visible at `as_of` are traded at the next open. The enforcer
   guarantees the data existed; it does not model the time needed to compute and route an
   order.

6. **Match timezone conventions.**
   Normalise the dataset and the `as_of` argument to one convention, UTC recommended. A
   mismatch raises rather than comparing incorrectly, because alt-data delivery times sit
   near midnight and a silent offset shifts availability by a whole day.

7. **Verify the guarantee empirically, not by inspection.**
   ```python
   pit = enforcer.get_point_in_time_data(as_of, include_effective_date=True)
   assert pit["effective_publication_date"].max() <= as_of
   ```
   Run it on every simulated day. Also run the negative control: count the rows a naive
   `event_date <= as_of` join would have admitted. On a 208-row, 60-day card panel that
   control admitted 1,543 row-observations the enforcer correctly withheld.

8. **Store snapshots if the vendor overwrites.**
   If the vendor rewrites history in place, versioned rows do not exist and no filter can
   reconstruct them. Persist a dated copy of each delivery; that archive is the only true
   point-in-time record you will have.

## Known Failure Modes

- **A mistyped publication column silently voiding the guarantee.** Falling back to
  `default_lag_days` when the named column is absent replaced a real 2024-06-01
  publication date with a 3-day guess and admitted the row on 2023-11-27.
- **Restatements returned twice.** Without a revision key, a query after a restatement
  returned both the original estimate and the revision for the same observation, and any
  aggregation double-counted it.
- **Calendar counting where the vendor meant business days.** A Friday event with a
  "3 day" lag surfaced on Monday instead of Wednesday — two days of look-ahead on every
  weekend in the sample.
- **Weekend events rolled backward.** Counting a Saturday event's business-day lag from
  the preceding Friday publishes a day before the vendor's pipeline has even started.
- **Holidays counted as working days.** A business-day offset without a holiday list
  treats Thanksgiving and Christmas as ordinary business days and publishes early.
- **Midnight publication stamps.** A bare date read as 00:00 handed a 09:30 decision a
  file that does not land until 18:00.
- **Publication before the event.** A row claiming to have been published before its own
  event occurred was accepted as a very short lag rather than rejected as corrupt.
- **Negative fallback lags.** `default_lag_days=-5` made data visible five days before the
  event it described.
- **Naive/aware timestamp mismatch.** Raised a bare pandas `TypeError` from deep inside a
  comparison rather than an actionable message.

## Production Implementation Reference

- Reference code: `scripts/alt_data_lag_enforcer.py` (`AltDataLagEnforcer`,
  `AltDataLagError`, `EFFECTIVE_PUBLICATION_COL`).
- Automated unit tests: `scripts/test_alt_data_lag_enforcer.py`.
