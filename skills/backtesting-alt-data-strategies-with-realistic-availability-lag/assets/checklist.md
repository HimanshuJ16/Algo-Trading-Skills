# Pre-Flight / Sign-off Checklist — backtesting-alt-data-strategies-with-realistic-availability-lag

Use this before considering the skill's implementation complete.

## Lag Provenance

- [ ] Has the vendor's data delivery SLA been verified — from the **contract**, not a datasheet?
- [ ] Has the contractual lag been confirmed against **observed file arrival times**?
- [ ] Is the lag quoted in calendar or business days, and does `lag_calendar` match?
- [ ] Is `default_lag_days` set equal to or greater than the maximum SLA delay?
- [ ] Is the **delivery time of day** recorded, and set via `availability_time`? A bare date means midnight, which is the most permissive possible reading.
- [ ] If `lag_calendar="business"`, has a `holidays` list been supplied? Business-day offsets skip weekends but not holidays.

## Point-in-Time Integrity

- [ ] Are we using `publication_date` as the primary Point-in-Time filter when available?
- [ ] Does construction **raise** if the publication column is mistyped, rather than falling back to a guessed lag? (Confirm by deliberately mistyping it once.)
- [ ] Does `get_point_in_time_data` correctly return an empty DataFrame if no data has been published yet?
- [ ] Has the guarantee been asserted across the whole backtest loop: `max(effective_publication_date) <= as_of` on **every** simulated day?
- [ ] Has the negative control been run — how many rows would a naive `event_date <= as_of` join have admitted?
- [ ] Do the dataset and the `as_of` argument share one timezone convention?

## Revisions and Data Shape

- [ ] Have we verified that the dataset does not overwrite historical values (revision bias)? If so, are we using daily snapshots?
- [ ] If the dataset carries versioned rows, is `revision_key_cols` set so a query returns the version known on that date rather than every revision at once?
- [ ] Is `lag_audit()["duplicate_event_rows_without_key"]` zero, or explained?
- [ ] Do multiple legitimate rows per event date (per merchant, region, category) survive — i.e. is the revision key the full logical identity, not the date alone?

## Evidence vs Assumption

- [ ] What share of rows is `fallback_rows` in `lag_audit()`? A high share means the PIT guarantee is an assumption, and the backtest's credibility is bounded by it.
- [ ] Is the audit output persisted alongside the backtest results, with the lag configuration it was produced under?

## Beyond the Lag

- [ ] Is the trading universe itself point-in-time? A correct lag over a hindsight-selected universe still leaks — see `backtest-look-ahead-in-universe-selection`.
- [ ] Have licensing, MNPI and consent-provenance questions been cleared separately? Lag enforcement is not a compliance control.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/backtesting-alt-data-strategies-with-realistic-availability-lag/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Vendor, product, contractual lag, calendar convention, delivery time: ___________________________
