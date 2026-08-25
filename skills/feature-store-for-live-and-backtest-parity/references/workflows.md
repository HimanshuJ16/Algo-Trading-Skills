# Deep Workflow Reference — feature-store-for-live-and-backtest-parity

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full procedure

### 1. Implement the shared core (`compute_features_from_window`)

A pure function of one window of closes returning the feature tuple. Both runtimes import
and call it. The mechanism is Rule #32 of *Rules of Machine Learning* — re-use the code
rather than reconcile the outputs — and everything downstream is a check that the reuse
was not quietly undone.

Pin these decisions in the function itself, with named constants:

- **`stddev_ddof`.** Population (0) or sample (1). Do not inherit whichever your library
  happens to default to: `pandas.rolling().std()` is `ddof=1`, TA-Lib is population. If a
  pandas batch prototype is later replaced by this core, an unmatched ddof shifts every
  Bollinger value with no error surface at all.
- **Undefined branches.** A flat window makes RSI 0/0 and the Bollinger band zero-width.
  Return an explicit neutral (RSI 50, %B 0.5) rather than letting an epsilon floor decide.
  An epsilon floor is also scale-dependent — a `1e-5` absolute floor means something
  different for a 20-rupee stock and a 70,000-point index.
- **No rounding.** Round for display, never inside the core. Rounding to 4 decimals makes
  every tolerance below roughly 5e-5 pass unconditionally.
- **Positive, finite inputs.** Validate closes before use. A zero close raises
  `ZeroDivisionError` from inside the return calculation, and a NaN close reaches
  `statistics.stdev` and raises an unrelated `AttributeError` — neither is actionable in a
  feed handler at 09:15.
- **Window-derivability.** Every feature must be a function of the window alone. A
  recursive estimator (Wilder RSI, EWMA volatility, Kalman state) is not, and no lookback
  makes it so; serve those by checkpointing the state instead.

### 2. Offline batch pipeline (`compute_batch_features`)

Validate the whole series first: non-empty, well-formed bars, strictly increasing
timestamps. An unsorted or duplicated batch series produces a feature matrix that no live
stream can reproduce, which surfaces later as an unexplained parity failure.

Build the window at index `i` as `bars[max(0, i - lookback + 1) : i + 1]` — right-closed
at the bar being labelled, so no future bar can enter. Emit one row per input bar and flag
`is_warm = len(window) >= lookback`.

A useful independent check on the right-closure: recompute the matrix on a truncated copy
of the series and confirm every surviving row is byte-identical. If truncation changes an
earlier row, some future bar was reaching backwards.

### 3. Online streaming pipeline (`compute_online_feature`)

Maintain a `deque(maxlen=lookback)` — a bounded ring buffer, not a list with `pop(0)`.

Enforce a timestamp watermark: reject any bar not strictly newer than the last ingested
one. This is the reconnect case, not a corner case. A websocket that re-delivers its last
bar after resubscribing would otherwise have it appended as a distinct observation, which
fabricates a `return_1d` of 0.0 *and*, because the buffer is bounded, evicts a genuine bar
from the far end — permanently shortening the effective history behind every later
feature. The engine cannot recover that bar; the feed must de-duplicate, or the engine
must refuse.

Seed with `warm_up(historical_bars)` before the first live inference, using the bars the
batch pipeline would have held immediately before the session's first bar. Until the
buffer is full, features carry `is_warm=False` and must not reach the model.

### 4. Assert parity (`validate_parity`)

Replay one series through both pipelines and compare each feature field, plus the
`is_warm` flag.

Two properties matter as much as the comparison:

- **Run the replay on a throwaway engine.** A validator that clears `self`'s ring buffer
  resets the live warm-up and leaves the validation series in the buffer. The self-check
  then *causes* the incident it was meant to prevent, on a live engine, silently.
- **Report which feature diverged.** "Max diff 0.03" across four features of different
  scales is not a diagnosis. Name the field, the index, both values, and the tolerance.

Start at `tolerance=0.0`. A shared core evaluating identical operations on identical inputs
is bit-identical; anything else means the sharing is not real. Raise the tolerance only
when the batch side has deliberately been re-implemented, and record why.

### 5. Close the gap replay cannot reach

Replay proves code parity on identical input. Production skew also arrives through the
input: revised vendor bars, late or dropped ticks, a different consolidation rule between
the historical API and the live socket, an adjustment applied to stored history but not to
the live feed. Log the feature vectors actually served, keyed by symbol and timestamp, and
reconcile them against the batch recomputation over the same window (Rules of ML #29).
That reconciliation, not the replay test, is what catches a vendor restating last
Tuesday's bars.

## Failure modes observed in production

- **Train-serve skew from dual code paths.** Two implementations agree when written and
  drift with every later edit. The backtest keeps reporting the pre-drift numbers.
- **The self-check as the incident.** Running a parity validator that mutates engine state
  against a live engine — warm-up lost, buffer replaced with the validation series, next
  inference computed on the wrong history.
- **Silent ddof mismatch.** A pandas batch prototype (`ddof=1`) promoted against a
  TA-Lib-shaped live path (population). Every Bollinger value slightly wrong, in one
  direction, with no error.
- **Rounding that hides the failure.** A 4-decimal round inside the shared core, validated
  at a 1e-6 tolerance. The assertion is structurally incapable of failing.
- **Duplicate bar on reconnect.** A fabricated zero return plus a silently truncated
  history, both indistinguishable from real values downstream.
- **Cold live start.** First `lookback` inferences on short-window estimators the model
  never saw, emitted with no marker distinguishing them from warm features.
- **Undefined mapped to an extreme.** RSI 100 on a flat, non-trading instrument, read by
  the model as maximum momentum.

## Production implementation reference

- Reference code: `scripts/feature_store.py` (`ParityFeatureStoreEngine`, `FeatureVector`,
  `Bar`, `FeatureParityMismatchError`, `BarSequenceError`).
- Automated unit tests: `scripts/test_feature_store.py`. Expected values there are derived
  independently of the implementation — the Bollinger fixture uses the closed form for the
  population variance of an arithmetic progression, `(n² − 1)d²/12`, and the volatility
  z-score fixture is constructed so its absolute returns are exactly `{0.01, 0.01, 0.01,
  0.02}`, giving `√3` by hand.
