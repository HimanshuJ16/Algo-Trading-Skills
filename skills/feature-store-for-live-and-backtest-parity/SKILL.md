---
name: feature-store-for-live-and-backtest-parity
description: Use when building ML feature pipelines to implement a single shared feature
  computation engine that keeps offline batch backtesting and online live streaming
  inference numerically identical, and to validate that parity automatically
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- feature-store
- backtest-live-parity
- train-test-skew
- feature-engineering
brokers_frameworks:
- Feast
- Hopsworks
- Pandas
- NumPy
- TA-Lib
- Custom Feature Engines
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a model is trained offline on historical bars and served online on a
live market data stream. Writing one feature path for the backtest (Pandas vectorization)
and a second for live trading (a stateful streaming loop) produces training-serving skew:
the two agree on the day they are written and drift apart with every subsequent edit.
Divergence in NaN handling, rolling-window boundaries, standard-deviation conventions, or
warm-up behavior moves live predictions away from the ones the backtest was validated on,
and the backtest keeps reporting the old, better numbers.

The remedy is structural, not a reconciliation report: one shared calculation core called
by both runtimes, plus an automated replay assertion that the two matrices agree.

## When NOT to Use

- **Path-dependent features that are not window-derivable.** Wilder's RSI, EWMA
  volatility, and Kalman filters carry recursive state whose value depends on where the
  history was started. They cannot be reproduced from a bounded ring buffer. Serve them by
  checkpointing the smoothed state across the batch/online boundary — see
  `offline-train-online-infer-deployment` — not by widening the lookback until the
  difference looks small.
- **Point-in-time correctness of the underlying data.** This skill makes two code paths
  agree; it does not make either one as-of correct. Fundamentals, index membership, and
  restated vendor history are `point-in-time-database-for-ml-training-data` and
  `point-in-time-fundamentals-data-joins`.
- **Cross-sectional or portfolio-level features.** The engine here is per-instrument and
  single-window; a rank across a universe needs the whole universe present at each
  timestamp, which a per-symbol ring buffer cannot supply.

## Prerequisites

- One shared feature-calculation module imported by both the backtest and the live runtime
  — not copied between them.
- A bounded rolling buffer sized to the lookback for the online path.
- A de-duplicated, chronologically ordered bar source. Duplicate or late bars are a data
  problem the feature store must reject, not absorb.
- An automated parity test wired into CI, so the assertion runs on every change to the
  feature code rather than at promotion time only.

## Workflow

1. **Implement the shared core** (`compute_features_from_window`) as a pure function of
   one window of closes. Both runtimes must call this function; neither may hold a private
   copy of the arithmetic. Fix the conventions here explicitly rather than inheriting a
   library default:
   - **Standard deviation ddof.** `pandas.rolling().std()` defaults to `ddof=1`; TA-Lib
     and `pandas_ta.bbands` use the population divisor (`ddof=0`). Pick one, name it in
     code, and assert it. Unmatched ddof is the most common concrete cause of the skew
     this skill exists to prevent, and it is silent — the bands are merely slightly wrong.
   - **Undefined cases.** A flat window makes RSI a 0/0 division and %B a zero-width
     band. Decide the value and write it down; the reference engine returns neutral
     (RSI 50, %B 0.5). TA-Lib returns RSI 0.0 for the same input, so a pipeline diffed
     against TA-Lib will disagree on flat windows and only on flat windows.
   - **No rounding inside the core.** Quantizing to four decimals makes any parity
     tolerance tighter than about 5e-5 unenforceable — the check passes because the
     evidence was thrown away.
2. **Run the batch pipeline** (`compute_batch_features`) over the historical bars. The
   window at index `i` must close on the right at bar `i`: `bars[i-lookback+1 : i+1]`.
   Reject an unsorted or duplicated series up front — a batch matrix built from unordered
   bars has no live stream that can reproduce it.
3. **Run the online pipeline** (`compute_online_feature`) over the live stream. Before the
   first inference, seed the buffer with the bars the batch pipeline would have held
   (`warm_up`). Reject any bar whose timestamp is not strictly newer than the last one
   ingested; a websocket replaying its last bar after a reconnect is the normal case, not
   an exotic one.
4. **Mask warm-up rows.** A window shorter than the full lookback yields a *different*
   estimator, and a window shorter than the minimum yields a placeholder. Both are flagged
   `is_warm=False`. Drop or mask them before training and before inference — a placeholder
   fed to a model is indistinguishable from a real reading once it is in the matrix.
5. **Assert parity** (`validate_parity`). Replay one bar series through both pipelines and
   compare every feature. With a genuinely shared core the difference is exactly 0.0, so
   run it at `tolerance=0.0` first; reserve a non-zero tolerance for a caller who has
   substituted a vectorized batch implementation whose float accumulation order differs.
   Run the validator on a throwaway engine so a live engine's buffer is never touched.
6. **Close the data-side gap that replay cannot reach.** Replay proves the two code paths
   agree on identical input. It says nothing about the input differing in production —
   vendor bar revisions, late or dropped ticks, a different consolidation rule, an
   adjustment applied to history but not to the live feed. Log the feature vectors
   actually served and reconcile them against the batch recomputation for the same
   timestamps (Rules of ML #29).

> Full step-by-step procedure with implementation detail: see `references/workflows.md`.
> Convention and standards table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Dual code paths.** Two feature implementations agree on day one and drift with every
  edit afterwards. Share the function; do not reconcile the outputs.
- **A parity check that mutates live state.** A validator that clears the engine's own
  ring buffer to run its replay resets the live warm-up and leaves the validation series
  in the buffer, so the next live inference runs on the wrong history — the self-check
  causes the outage. Replay on a separate instance.
- **Rounding inside the shared core.** Rounding makes the parity assertion pass by
  destroying the digits that would have failed it. Round for display, never before the
  comparison.
- **Unmatched standard-deviation ddof.** A pandas batch path and a TA-Lib-shaped live path
  disagree on every Bollinger value, with no error and no NaN — just bands a few basis
  points off, all day, in one direction.
- **Duplicate bar replay after a reconnect.** Appending a re-delivered bar as a fresh
  observation fabricates a 0.0 return and, because the buffer is bounded, evicts a real
  bar — permanently shortening the history behind every later feature. De-duplicate on
  timestamp and reject anything not strictly newer.
- **Cold live start.** Beginning a session with an empty buffer means the first `lookback`
  inferences run on short-window estimators the model never saw in training. They look
  like ordinary feature values.
- **Undefined values silently rendered as extremes.** RSI on a flat window is 0/0. Mapping
  it to 100 tells the model an illiquid, non-trading instrument is maximally overbought.
- **Look-ahead reintroduced downstream.** Features at timestamp t include bar t's close,
  so they are actionable at or after that close. Using them to size bar t's open is
  look-ahead the feature store cannot detect — see `lookahead-bias-elimination`.
- **Treating replay parity as proof of production parity.** It is a code-parity test. Feed
  differences are invisible to it.

## Verification

- Run `python scripts/test_feature_store.py` and confirm all tests pass.
- Compute the batch matrix over a historical series, replay the same series through
  `compute_online_feature()`, and confirm `validate_parity(bars, tolerance=0.0)` returns
  True — with a shared core the agreement is exact, not merely within epsilon.
- Confirm `validate_parity()` raises `FeatureParityMismatchError` when the online path is
  genuinely perturbed, and that the message names the diverging feature and index. A test
  that raises the error itself verifies nothing.
- Confirm that running `validate_parity()` on a warmed engine leaves `online_ring_buffer`
  byte-identical and the engine still warm.
- Confirm a re-delivered bar and an out-of-order bar both raise `BarSequenceError`, and
  that the buffer depth is unchanged afterwards.
- Confirm the first `lookback_period - 1` batch rows report `is_warm=False`, and that a
  flat window returns RSI 50.0 rather than 100.0.
- Confirm a non-finite or non-positive close is rejected with an actionable error rather
  than a `ZeroDivisionError` from inside the return calculation.

## Related Skills

- `feature-engineering-without-leakage` — leakage inside the feature definition itself, which parity preserves rather than fixes
- `offline-train-online-infer-deployment` — serving path-dependent models whose state is not window-derivable
- `research-environment-vs-production-environment-parity` — the same divergence problem one level up, at the environment
- `point-in-time-database-for-ml-training-data` — as-of correctness of the inputs this engine consumes
- `websocket-reconnection-with-state-recovery` — the reconnect that delivers the duplicate bars this engine rejects
- `backtest-determinism-and-reproducibility` — reproducibility of the batch side in isolation
- `lookahead-bias-elimination` — the look-ahead a caller can still introduce downstream
