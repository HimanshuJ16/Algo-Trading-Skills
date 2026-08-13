# Deep Workflow Reference — backtest-outlier-and-bad-tick-filtering

This file holds the full technical procedure referenced by `SKILL.md`. The detection rule,
its sourcing, and threshold calibration live in `references/standards.md`.

## Full Procedure

1. **Configure against the instrument, not the defaults.**
   ```python
   f = OutlierBadTickFilter(
       window_size=21,                 # >= 3
       z_threshold=5.0,                # NIST/Iglewicz-Hoaglin suggest 3.5 for generic data
       max_single_tick_jump_pct=5.0,   # calibrate against the venue band, not the 20.0 default
       max_consecutive_drops=3,        # >= 2
       min_deviation=0.01,             # the instrument's tick size
   )
   ```
   Every invalid combination raises `OutlierFilterError` at construction rather than silently
   degrading. In particular `window_size=0` is rejected because `prices[-0:]` is `prices[0:]`,
   which would quietly widen the window to the entire history, and `max_consecutive_drops=1`
   is rejected because it makes every outlier an instant regime change, disabling all purging.

2. **Reject non-finite and non-positive prices first.**
   NaN must be handled before any comparison: every comparison against NaN evaluates False, so
   an unguarded filter accepts it, writes it into the cleaned series, and then corrupts every
   subsequent median. Counted separately as `non_finite_count` and `non_positive_count`.

3. **Apply the single-tick jump test against the last accepted price.**
   The reference is the previous *accepted* price, not the previous input price, so a cluster
   of bad prints cannot walk the reference away from the true level.

4. **Apply the trailing MAD test once the window has filled.**
   $|P_i - \tilde{x}| > Z_{\max}\cdot\text{MAD}/0.6745 + \gamma$. Working in price units rather
   than dividing by MAD means a zero MAD does not raise `ZeroDivisionError`. When MAD is zero
   *and* $\gamma$ is zero the scale estimate has degenerated and the test is skipped —
   `mad_test_skipped_count` records how often, so a series screened only by the jump rule is
   visible rather than silently unprotected.

5. **Classify a run of consecutive outliers.**
   Reaching `max_consecutive_drops` reinterprets the run as a genuine level shift. Two things
   then happen: the prints purged on the way in are restored (unless
   `restore_ticks_on_regime_change=False`), and the MAD window restarts at the new level. The
   restart matters — without it the window stays contaminated by pre-shift prices for
   `window_size` more ticks and keeps re-flagging good prints.
   A run that never reaches the threshold before the series ends cannot be confirmed and stays
   purged.

6. **Realign every parallel array.**
   ```python
   cleaned, report = f.filter_prices(prices)
   timestamps = [raw_timestamps[i] for i in report.kept_indices]
   ```
   The function returns bare prices. Reusing the original timestamp array against the cleaned
   series silently shifts every observation by the number of purges before it.

7. **Validate the warm-up separately.**
   The first `window_size` accepted ticks get no MAD screening, and the same gap reopens for
   `window_size` ticks after every confirmed level shift because the window restarts there.
   Cross-check those stretches against another vendor
   (`data-vendor-cross-validation-for-backtests`) or discard them.

8. **Measure the false-positive rate before trusting the settings.**
   Run the filter over a segment known to be clean and inspect `cleanliness_pct`. A level-based
   test on a trending series flags some legitimate moves; you need to know your rate, not
   assume it is zero.

9. **Keep the raw series.**
   Persist the raw prices, the purged indices, and the filter configuration alongside the
   cleaned series so any deleted print can be reviewed and the pass replayed.

## Failure Modes Observed in Production

- **NaN accepted and propagated.** `nan <= 0` is False and `abs(nan - prev)/prev > pct` is
  False, so an unguarded filter writes NaN into the clean series. Every later window that
  contains it produces a meaningless median, because `sorted()` on a list containing NaN gives
  an arbitrary order.
- **A bad first print anchoring the file.** With no history, the first tick is accepted
  unconditionally. The genuine prices that follow then look like enormous jumps away from it.
  Before this was fixed, `[10.0, 100.0, 100.1, 100.2, ...]` cleaned to
  `[10.0, 100.2, ...]` — the erroneous print survived and two real ticks were deleted.
- **Real data destroyed at every genuine gap.** A consecutive-drop rule that does not restore
  purges the leading ticks of every split, halt reopen, or news gap. Compounded by a
  contaminated window, a single level shift cost 22 real ticks in measurement.
- **Silent configuration degradation.** `max_consecutive_drops=1` turns the filter into a
  no-op that purges nothing but non-positive prices; `window_size=0` silently expands the
  window to the whole history. Both now raise.
- **Flat-window blindness.** In a window where over half the prices are identical, MAD is zero
  and the modified Z-score is undefined. A 5% erroneous print in a flat series is invisible to
  both rules unless $\gamma$ is set.
- **Timestamp misalignment.** Filtering prices without realigning timestamps shifts every
  observation after the first purge, which is worse than the bad tick it removed.
- **Over-filtering genuine crash prints.** Purging a real flash-crash print means a backtest
  stop-loss never fires. See `SKILL.md` "When NOT to Use".

## Production Implementation Reference

- Reference code: `scripts/outlier_filter.py` (`OutlierBadTickFilter`, `FilteredTickReport`,
  `OutlierFilterError`).
- Automated unit tests: `scripts/test_outlier_filter.py`.
