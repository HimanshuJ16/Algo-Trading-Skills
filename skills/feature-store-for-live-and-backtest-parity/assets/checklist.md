# Pre-Flight / Sign-off Checklist — feature-store-for-live-and-backtest-parity

Use this before considering the skill's implementation complete.

## Shared core

- [ ] **Single point of truth:** `compute_features_from_window()` is *imported* by both the
      batch and online runtimes — no second copy of the arithmetic anywhere in the repo.
- [ ] **Standard deviation ddof pinned:** the divisor is set explicitly in code (population
      vs sample) and matches whatever the other side of the pipeline uses. Confirm against
      the actual library defaults, not from memory.
- [ ] **Undefined branches decided:** flat-window RSI (0/0) and zero-width Bollinger bands
      return documented neutral constants, not an epsilon-floored extreme.
- [ ] **No rounding inside the core:** features are returned at full float precision.
- [ ] **Every feature is window-derivable:** no recursive/path-dependent estimator is being
      approximated from a bounded buffer.

## Pipelines

- [ ] **Batch windows are right-closed:** recomputing on a truncated series leaves every
      surviving row byte-identical (no future bar reaches backwards).
- [ ] **Input validation:** empty series, non-finite or non-positive prices, and
      non-chronological timestamps are rejected with actionable errors.
- [ ] **Bounded ring buffer:** the online path uses a fixed-capacity buffer sized to the
      lookback.
- [ ] **Duplicate/late bars rejected:** a re-delivered bar (websocket reconnect) and an
      out-of-order bar both raise, and leave buffer depth unchanged.
- [ ] **Warm-up seeded:** `warm_up()` is called with real history before the first live
      inference, and `is_warm` is True before any prediction is acted on.
- [ ] **Warm-up rows masked:** `is_warm=False` rows are dropped or masked in both training
      and inference — verified, not assumed.

## Parity assertion

- [ ] **Exact parity:** `validate_parity(bars, tolerance=0.0)` passes. If a non-zero
      tolerance is required, the reason is recorded and the batch side is a deliberate
      re-implementation.
- [ ] **The validator can actually fail:** perturbing the online path raises
      `FeatureParityMismatchError`, and the message names the diverging feature and index.
      A test that raises the error itself proves nothing.
- [ ] **The validator is side-effect free:** running it on a warmed live engine leaves
      `online_ring_buffer` unchanged and the engine still warm.
- [ ] **Wired into CI:** the parity assertion runs on every change to the feature code, not
      only at promotion.

## Data-side parity (what replay cannot prove)

- [ ] **Serving-time feature logging** is in place, and served vectors are reconciled
      against the batch recomputation for the same symbol/timestamps.
- [ ] **Feed differences considered:** vendor bar revisions, late/dropped ticks, differing
      consolidation rules, and corporate-action adjustments applied to history but not to
      the live feed.

## Automated testing

- [ ] Run `python scripts/test_feature_store.py` and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Parity tolerance used and justification: ___________________________
