# Deep Workflow Reference — lookahead-bias-elimination

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Pin the decision timestamp and execution lag.**
   - Write down the exact instant the decision is taken and the number of bars before it can fill. Every check below is relative to those two numbers, and neither is recoverable from the code.
   - An end-of-bar decision may use bar $T$'s Close; an intra-bar decision may not. The same indicator column is legitimate under one reading and a leak under the other.

2. **Same-bar fill contamination audit.**
   - Run `LookaheadBiasAuditor.audit_backtest_timing(df, timestamp_col=..., indicator_cols=[...])`.
   - Reports `SAME_BAR_FILL_CONTAMINATION` where an active signal's fill price equals that bar's Close, High, or Low, using a **relative** tolerance so the same call works at crypto and FX price scales.
   - Rows are addressed **positionally**, so the result is identical on a `DatetimeIndex`, a sliced integer index, and a `RangeIndex`.
   - Missing `signal_col`/`fill_price_col`, or all three reference price columns, **raises**. A partially available set produces an `UNDETERMINED` finding naming what went unchecked. Neither condition is ever reported as a clean audit.
   - `UNDETERMINED` is also emitted for NaN signals (treated as inactive, but reported), for active signals whose fill price is NaN or non-numeric, and for active signals where every available reference price is NaN — that last case matched nothing for want of a comparison, not for want of a violation.

3. **Detector calibration — do this before believing any clean result.**
   - `run_timing_calibration(df)` overwrites every active signal's fill with that bar's Close and returns the fraction the auditor caught.
   - **1.0 is the only acceptable result.** A lower value is a measurement of how blind the screen is on this particular frame, and it discounts every clean verdict by the same amount.

4. **Signal-to-execution bar alignment.**
   - `LookaheadBiasAuditor.align_signal_execution(df, symbol_col=..., timestamp_col=..., execution_lag=1)` shifts the signal forward and writes the execution price from that bar's Open.
   - Pass `symbol_col` for any stacked (long-format) panel. Without it the shift runs down the whole frame and one instrument's last signal becomes the next instrument's first executed signal.
   - Pass `timestamp_col` so ascending order is verified per instrument. The shift is positional; an out-of-order frame aligns signals to the wrong bars, and the call raises rather than doing so silently.
   - `execution_lag=0` and negative lags are rejected — those are the defect, not a configuration.
   - The signal's dtype is preserved (bool stays bool, int stays int), and `fill_price` is written **only on executing bars**.

5. **Indicator warm-up validation.**
   - Prefer `indicator_cols=[...]`, which flags any active signal sitting on a NaN indicator, over the positional `warmup_periods` count, which requires guessing.
   - `rolling(n)` warms up on its own (`min_periods` defaults to the window size). `ewm(...)` does not (`min_periods` defaults to 0). A `fillna()` on either destroys the evidence.

6. **Delivered-column causality fingerprint.**
   - `audit_indicator_causality(df, price_col, indicator_col)` reports `CENTERED_ROLLING_WINDOW` when the column matches `rolling(w, center=True).<agg>()` but not the trailing equivalent, and `TIMESTAMP_CAUSALITY_BREACH` when it equals `price.shift(-k)`.
   - Cost is `O(max_window x len(aggregations))` rolling passes; lower `max_window` on very long series.
   - It recognises those two signatures only. Silence is not a clean bill of health.

7. **Point-in-time join verification.**
   - Non-price features (fundamentals, corporate actions, restated macro series, IV surfaces) must be joined on **publication** timestamp, never period-end or calendar date.
   - `check_feature_timestamps(feature_timestamps, decision_ts)` / `audit_feature_timestamps(...)` compare declared availability against the decision instant. A feature stamped at exactly the decision timestamp is a breach by default; `allow_exact_matches=True` relaxes that only when the timestamps already include dissemination latency.

8. **Strategy-level leak calibration.**
   - `run_leak_calibration(df, target_col)` adds the target shifted one bar back — a known one-bar-forward leak. Run the backtest on that frame; if performance does not inflate dramatically, the backtest is not sensitive enough for its clean results to mean anything.
   - This calibrates the **backtest**. Step 3 calibrates the **auditor**. Both are needed and neither substitutes for the other.

9. **Dataset-level separation.**
   - Confirm no threshold, feature selection, or hyperparameter search was run across the test period. Nothing in this module can see that — see `walk-forward-validation-setup`.

## Known Failure Modes

- **Same-Bar Close Fills:** Generating a signal from bar $T$'s Close and filling at bar $T$'s Close, assuming impossible zero-latency same-bar execution. Both backtrader and backtesting.py default to next-bar Open and expose same-bar-close only as a named opt-in.
- **Right-Closed Window Misread as Excluding the Current Bar:** pandas rolling windows are right-closed by default, so `close.rolling(20).mean()` at bar $T$ contains `close[T]`. Legitimate for an end-of-bar decision; a leak for an intra-bar one.
- **Centered Vectorized Rolling Windows:** `rolling(w, center=True)` averages roughly $(w-1)/2$ bars of the future into every value. `center` defaults to `False`, so this is always deliberate.
- **Unwarmed EWM Indicators:** `ewm()` defaults `min_periods=0` and returns a value on bar 0 from a single observation, unlike `rolling()`. Trading that value is trading noise with a confident-looking number attached.
- **Panel Shift Across Instruments:** `df['signal'].shift(1)` on a stacked multi-symbol frame moves one symbol's last signal onto the next symbol's first bar.
- **Post-Restated Fundamental Data:** Joining financial statements by fiscal quarter date rather than the actual filing date.
- **Index-Label Arithmetic:** Comparing an index *label* against a warm-up bar count. On a datetime-indexed frame there is no meaningful comparison, and code that attempts one silently degrades — this module addresses rows positionally for exactly that reason.

## Production Implementation Reference

- Reference code: `scripts/leak_audit.py` — `LookaheadBiasAuditor` (`audit_backtest_timing`, `align_signal_execution`, `run_timing_calibration`, `run_leak_calibration`, `audit_indicator_causality`), `LookaheadViolationType`, `LookaheadAuditFinding`, `audit_feature_timestamps`, `check_feature_timestamps`, `inject_forward_leak`.
- Automated unit tests: `scripts/test_leak_audit.py`.
- Screen limitations and regulatory scope: `references/standards.md`.
