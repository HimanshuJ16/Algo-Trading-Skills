---
name: lookahead-bias-elimination
description: >-
  Use when writing or auditing backtest code, to ensure no signal or decision uses
  information unavailable at the decision timestamp, starting with a bar's own close.
  Universe-selection lookahead is backtest-look-ahead-in-universe-selection.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, lookahead-bias, execution-timing, point-in-time
  brokers_frameworks: "Python pandas; Python NumPy; backtrader; backtesting.py"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this on every backtest implementation before trusting its results, and especially when an agent generates backtest code from a natural-language strategy description — lookahead bias is the single most common reason a backtest shows profitable results that evaporate or reverse in live trading, and it is easy to introduce without noticing because the code often "looks correct" (right column names, right indicators) while silently peeking at future data.

The governing rule is a timing rule, not a statistical one: **every value that feeds a decision taken at time T must have been observable strictly before T, and the resulting order cannot fill before the next bar.** Both mainstream Python backtest engines encode the second half of that as their default — backtrader matches an order issued during `next` against "the next incoming price which is the `open` price" of the following bar, and `backtesting.py` documents `trade_on_close` as defaulting to `False`, meaning "market orders are filled on the next bar's open." Same-bar-close execution exists in both, but only as an explicitly enabled cheat. If your hand-rolled pandas backtest fills at the same bar's close, you have silently opted into something both engines make you ask for.

## When NOT to Use

- **For statistical target contamination in an ML feature set.** A feature that is a copy or monotone transform of the label is a *feature* defect, screened by correlation, separation, and prefix-invariance tests — see `feature-engineering-without-leakage`. This skill screens *timing*.
- **For train/test boundary leakage.** Parameters tuned across the split, features selected on the full sample, or overlapping labels are dataset-level contamination — see `walk-forward-validation-setup` and `hyperparameter-tuning-without-target-leakage`.
- **For universe-selection lookahead.** "Top 50 by market cap" applied with today's membership is its own failure mode — see `backtest-look-ahead-in-universe-selection` and `survivorship-bias-free-universe-construction`.
- **As proof that a backtest is causal.** Every screen here is a candidate filter. See *What This Audit Cannot Detect*.
- **As a substitute for modelling execution cost.** Removing the timing leak does not make the fill realistic; slippage, latency, and partial fills are `execution-realistic-simulation`.

## Prerequisites

- A written definition of the **decision timestamp** — the exact moment the decision is made, not the bar it is associated with. "At the close of bar T" and "10 minutes into bar T" impose different cutoffs on the same data.
- Full historical OHLCV or tick data with accurate timestamps, in ascending chronological order, and **per instrument** if the frame is a stacked panel.
- A **publication/availability timestamp** — not just an effective or period-end date — on every non-price series being joined.
- `pandas` and `numpy`. No other dependency.

## Workflow

1. **Fix the decision timestamp and the execution lag in writing, before reading any code.** Everything below is measured against them. A decision taken at bar T's close executes at bar T+1's open at the earliest; a decision requiring a round trip longer than one bar executes later still.

2. **Audit the delivered backtest frame with `LookaheadBiasAuditor.audit_backtest_timing()`.** It reports `SAME_BAR_FILL_CONTAMINATION` when an active signal's fill price equals that same bar's Close, High, or Low.
   - **Decision point — a missing column raises, it does not return "clean".** If `signal`/`fill_price` are absent the call raises; if only some of Close/High/Low are present, or a signal bar's prices are all NaN, you get an explicit `UNDETERMINED` finding naming what could not be checked. Read those findings; they are not clean results.
   - **Decision point — a flagged row is not automatically a bug.** The screen compares prices, not provenance. When bar T+1 opens exactly at bar T's close — routine in continuous futures, crypto, and any synthetically stitched series — a correctly aligned fill is indistinguishable from a same-bar one. Confirm against the alignment logic before "fixing" correct code.

3. **Calibrate before believing a clean result — `run_timing_calibration()`.** It writes the same-bar close into every active signal's fill and returns the fraction the auditor caught. **1.0 is the only acceptable answer.** Anything lower means the screen is partly blind on this frame (usually non-numeric or NaN prices) and its clean verdict carries exactly that much less assurance.

4. **Repair alignment with `align_signal_execution()` rather than by hand.**
   - **Decision point — a stacked panel must pass `symbol_col`.** A positional `shift(1)` down a long-format frame carries the last signal of one instrument onto the first bar of the next. The function groups when told to and warns when it cannot verify order.
   - **Decision point — `execution_lag` is a modelling choice, not a constant.** 1 is the engine default; raise it when the decision cannot reach the venue within one bar. A lag of 0 is rejected outright.
   - Fill price is written **only on bars that actually execute**, so a later audit cannot match a price on a bar where nothing was filled.

5. **Audit indicator warm-up with data, not with a guessed bar count.** Pass `indicator_cols` and every active signal sitting on a NaN indicator is reported. This matters because pandas defaults differ: `rolling(20)` has `min_periods` defaulting to the window size, so it is NaN for the first 19 bars and warms up correctly on its own — whereas `ewm(span=20)` has `min_periods` defaulting to **0** and emits a value from the very first bar. A `fillna()` or `min_periods=1` on an indicator converts a safe NaN into a confident number computed from almost nothing.

6. **Fingerprint suspicious indicator columns with `audit_indicator_causality()`.** It reports `CENTERED_ROLLING_WINDOW` when the column matches `rolling(w, center=True).<agg>()` but not the trailing equivalent, and `TIMESTAMP_CAUSALITY_BREACH` when it equals `price.shift(-k)`. pandas documents `center` as defaulting to `False`, so a centred window is always a deliberate argument — and it averages about `(w-1)/2` bars of the future into every value.
   - **Decision point — a trailing window is right-closed and therefore includes bar T itself.** pandas documents `closed=None` as behaving as `'right'`, "meaning the last point is included in the calculations." That is legitimate for a decision taken *after* bar T closes and is same-bar lookahead for one taken *during* it. Which it is depends entirely on step 1.

7. **Check declared availability timestamps with `check_feature_timestamps()` / `audit_feature_timestamps()`.**
   - **Decision point — a feature stamped at exactly the decision timestamp is a breach by default.** A bar stamped at its own close time is not observable to a decision taken at that instant. `allow_exact_matches` defaults to `False`, matching the as-of merge convention in `feature-engineering-without-leakage`. Set it `True` only when the timestamps are already receipt times including dissemination latency.
   - Join fundamentals, restated macro series, corporate actions, and IV surfaces by **publication** timestamp, never by period-end or calendar date — see `point-in-time-fundamentals-data-joins`.

8. **Separately confirm that no dataset-level tuning crossed the test boundary.** Threshold selection, feature selection, or hyperparameter search run over the full sample is lookahead at the dataset level, invisible to every per-bar screen above — `walk-forward-validation-setup`.

> Full procedure: see `references/workflows.md`.
> Engine behaviour, citations, and audit limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## What This Audit Cannot Detect

Stated explicitly, because a sign-off checklist invites the opposite reading:

- **A leak whose numbers happen to look causal.** The same-bar screen compares the fill against three prices of one bar. A fill taken from bar T+1's high, or from a mid-price never stored in the frame, matches nothing and passes.
- **Any indicator built in a way the fingerprint does not recognise.** `audit_indicator_causality` knows two signatures — centred window and negative shift — against one price column. An EWMA, a multi-column construction, or a resampled series fits neither and returns no findings. That is absence of evidence.
- **Restated raw history.** If the vendor's "historical" field was itself revised after the fact — adjusted closes, backfilled fundamentals — every column is causal *within the delivered frame* and every screen passes. Only the publication timestamp reveals it. See `adjusted-vs-unadjusted-price-series-pitfalls`.
- **A frame whose row order is not time order.** Every shift and every position is computed by row. `align_signal_execution` raises on an out-of-order frame only when given `timestamp_col`; without it, order is assumed.
- **Dataset-level tuning leakage** — that is `walk-forward-validation-setup`'s domain.

## Common Pitfalls

- **Using `df['close']` to compute a signal and also as the fill price for the same row.** Same-bar lookahead disguised as normal-looking code, and the single most common form.
- **Assuming a trailing rolling window excludes the current bar.** It does not: pandas rolling is right-closed by default, so `close.rolling(20).mean()` at bar T contains `close[T]`. Legitimate for an end-of-bar decision, a leak for an intra-bar one.
- **Centred or forward-looking window arguments** — `rolling(..., center=True)`, `bfill()`, `interpolate()` — reaching across the current bar.
- **Trusting `ewm()` to warm up like `rolling()`.** `ewm` defaults `min_periods=0` and returns a value on bar 0 computed from one observation; `rolling(n)` returns NaN until bar n-1. Filling an indicator's NaNs converts an honest gap into a confident number.
- **Shifting a stacked multi-symbol panel positionally.** `df['signal'].shift(1)` on a long-format frame moves one instrument's last signal onto the next instrument's first bar.
- **Reading zero findings as "no lookahead."** Zero findings means no screen fired. Calibrate first (step 3); an auditor that cannot see an injected violation cannot see a real one.
- **"Fixing" a coincidental flag.** In a gapless series a correct next-bar-open fill equals the previous close. Check the alignment code, not just the finding count.
- **Joining point-in-time-sensitive data by calendar date** rather than publication timestamp, then attaching a record stamped at exactly the decision time.
- **Tuning parameters against the full history** and reporting the tail of it as out-of-sample.
- **Assuming zero latency between signal and fill** rather than modelling a realistic delay — `execution-realistic-simulation`.

## Verification

- **Same-bar detection.** Build 20 bars whose OHLC values do not coincide across rows, set `fill_price = close`, and confirm `audit_backtest_timing` returns one `SAME_BAR_FILL_CONTAMINATION` per active signal, with `row_index` equal to the **positional** offset. Repeat with `fill_price = high` and `= low`; all three must be detected.
- **Positional addressing.** Repeat the above on a `DatetimeIndex` and on an integer index starting at 500. The findings must be identical — a row at position 9 reports `row_index == 9`, never `0`.
- **Fail-loud.** Confirm that dropping `signal`, `fill_price`, or all three reference price columns **raises**, and that dropping only `high`/`low` yields an `UNDETERMINED` finding naming them.
- **Price scale.** Confirm two FX quotes one pip apart (0.000120 vs 0.000121) are *not* flagged, and that an identical crypto price (95,000.25) *is* — the tolerance is relative, so both hold with the same constant.
- **Calibration.** Confirm `run_timing_calibration` returns exactly `1.0` on a well-formed frame and `2/3` when one of three signal bars has a NaN close.
- **Alignment.** Confirm a signal at position 2 executes at position 3 at that bar's open, that `fill_price` is NaN on every non-executing bar, that `execution_lag=3` moves it to position 5, that `execution_lag=0` raises, and that the aligned frame re-audits with zero same-bar findings.
- **Panel safety.** On a two-symbol stacked frame with a signal on the first symbol's last bar, confirm `symbol_col=` yields an all-zero `executed_signal`, while the ungrouped call leaks it onto the second symbol's first bar.
- **Fingerprint.** Confirm `rolling(9, center=True).mean()` and `rolling(11, center=True).max()` are reported, `rolling(9).mean()` is not, `shift(-2)` is reported as `TIMESTAMP_CAUSALITY_BREACH`, and `ewm(span=10).mean()` returns nothing — the documented blind spot.
- **Timestamps.** Confirm a feature stamped at exactly the decision timestamp is reported by default and cleared under `allow_exact_matches=True`, and that a tz-aware/tz-naive comparison raises a `ValueError` naming the problem rather than a bare `TypeError`.
- **End-to-end.** Deliberately introduce a one-bar-forward leak (`run_leak_calibration()`) into a test copy of the strategy and confirm it measurably inflates backtest performance. If an obvious cheat does not change results, the backtest itself is not sensitive enough for its clean results to mean anything.
- **Walk-forward gap.** Compare in-sample against strict walk-forward out-of-sample performance (`walk-forward-validation-setup`); a large unexplained gap indicates residual lookahead even after every check above.
- Run `python -m unittest discover -s skills/lookahead-bias-elimination/scripts` and confirm a 100% pass rate.

## Related Skills

- `feature-engineering-without-leakage`
- `walk-forward-validation-setup`
- `execution-realistic-simulation`
- `backtest-look-ahead-in-universe-selection`
- `point-in-time-fundamentals-data-joins`
- `adjusted-vs-unadjusted-price-series-pitfalls`
- `survivorship-bias-free-universe-construction`
