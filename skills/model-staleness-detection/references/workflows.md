# Deep Workflow Reference — model-staleness-detection

This file holds the full technical procedure referenced by `SKILL.md`. Load this
when actually implementing the skill, not just when deciding whether it applies.

Reference implementation: `scripts/staleness_monitor.py`
(`ModelStalenessMonitor`, `ModelHealthStatus`, `FeatureDriftStatus`,
`DriftMethod`, `FeatureDriftResult`, `ModelStalenessReport`,
`population_stability_index`, `wilson_lower_bound`). No third-party
dependencies. Automated tests: `scripts/test_staleness_monitor.py`.

## Full Procedure

### 1. Log predictions and realised labels

Record every live inference — prediction, feature snapshot, and the realised
outcome once it exists — to durable storage, not only to the monitor's memory.

- Record **all** inferences, not only the ones that became trades. A sample
  restricted to traded signals is conditioned on the strategy's other filters
  and measures those filters as much as the model.
- Record the outcome only once it has **fully** realised. Scoring against a
  partially formed bar is look-ahead bias inside the monitor itself.
- Record predictions as **discrete labels**. `record_prediction()` rejects
  floats: bucketing a regressor's output is a modelling decision that belongs
  where its thresholds are visible, not inside an `int()` cast that maps 1.2
  and 1.7 onto the same class.
- On start-up, reload the window with `restore_history()`. `window` counts
  *realised outcomes*, not calendar days — a model scoring 500 instruments a
  day fills a 60-entry window in minutes, while a 20-day-horizon model needs
  months to fill one. Size it to the horizon you intend to monitor over.

### 2. Rolling accuracy and precision

`get_rolling_accuracy()` and `get_rolling_precision(label)` over the trailing
window. Never a cumulative all-time average — it dilutes recent degradation
with historical good performance, and the dilution grows with the model's age.

- Below `min_predictions` realised outcomes the monitor reports
  `INSUFFICIENT_DATA` and sizes at `warmup_sizing_multiplier` (default `0.0`,
  fail-closed). `get_rolling_accuracy()` returns `None`, not `1.0`.
- Report precision on the traded side wherever the strategy acts on one
  direction. Under class imbalance, accuracy scores the base rate: a model that
  always predicts the majority direction looks as good as the imbalance.
- Read `accuracy_lower_bound` (one-sided Wilson, default 95%) next to the point
  estimate. 33 correct out of 60 is a point estimate of 0.550 and a lower bound
  of 0.444 — a window that looks comfortably above a 0.52 threshold is
  statistically consistent with a coin flip.

### 3. Feature drift

`compute_feature_drift(name, live_values)` per feature, against the baseline
registered by `set_training_baseline()`.

- Register `{"reference_sample": [...]}` — the training values themselves —
  wherever possible. That gives true binned PSI, sensitive to location, scale
  **and** shape.
- `{"mean": m, "std": s}` is accepted and falls back to the Gaussian closed form
  `PSI = z**2`, which is blind to any change that leaves the mean where it was.
  `FeatureDriftResult.method` records which was used; surface it on the
  dashboard so the weaker measurement is visible rather than implied.
- Bins are reference quantiles with **unbounded outer edges**, so live values
  outside the historical support are counted rather than discarded, and the
  binner detects quantile collapse on sparse indicators (a 95/5 regime flag
  de-duplicates to one bin, where PSI would be identically 0.0).
- The report's trigger is the per-feature **maximum** (`max_psi`,
  `max_psi_feature`). The count of drifting features is reported but never
  decides: one broken feature in a hundred is a broken pipeline, and a mean
  dilutes it below any threshold.

### 4. Unmeasurable features

Four distinct faults, none of which is "no drift observed":

| `FeatureDriftStatus` | Cause | Health consequence |
|---|---|---|
| `NON_FINITE` | NaN/Inf in the live batch | **Halt.** The model is being scored on it; its output is not a signal. |
| `NO_BASELINE` | Feature name matches no registered baseline | Degrade + alert. Monitoring is broken; the model may be fine. |
| `NO_LIVE_DATA` | Empty live batch — the feature stopped being produced | Degrade + alert. |
| `INSUFFICIENT_LIVE_DATA` | Fewer than `min_live_values` observations | Degrade + alert; PSI is not computed. |
| `DEGENERATE_BASELINE` | Training feature was constant | Degrade + alert; nothing can be measured against it. |
| `MISSING_FROM_BATCH` | A registered feature was absent from the supplied batch | Degrade + alert. Monitoring 3 of 5 registered features silently looks exactly like monitoring all 5 and finding nothing. |

Passing `None` as the batch is accuracy-only monitoring. Passing a dict —
including an empty one — asserts the registered features are being monitored,
so any that are absent are reported rather than skipped.

In every case `psi_score` is `None`, never `0.0` — a dashboard plots 0.0 as
"no drift observed", which is the opposite of what a dead feed means.

### 5. Status precedence and the sizing matrix

`evaluate_health()` resolves, highest precedence first:

1. A **latched halt** — until `clear_halt(operator, reason)`.
2. Any feature with **non-finite** live values.
3. Any single feature at or above `psi_halt_threshold`.
4. Accuracy below `min_accuracy_threshold` on `consecutive_breaches_to_halt`
   consecutive evaluations.
5. Fewer than `min_predictions` realised outcomes → `INSUFFICIENT_DATA`.
6. Warning band, any drifting feature, or any unmeasurable feature →
   `DEGRADED_WARNING`.

| Status | `sizing_multiplier` | Meaning |
|---|---|---|
| `HEALTHY` | 1.0 | Within expected parameters. |
| `DEGRADED_WARNING` | 0.5 | Accuracy in the warning band, or drift at or above the warning threshold. |
| `INSUFFICIENT_DATA` | `warmup_sizing_multiplier` (default 0.0) | Not enough realised outcomes to judge. Not the same as healthy. |
| `HALTED_STALE` | 0.0 | Halt the signal's contribution; retrain and shadow-validate. |

Three properties of the state machine that exist for operational reasons:

- **A halt latches** (`latch_halt=True`). A model that halted is retrained and
  shadow-validated, not waved back in because the next window looked better.
  `clear_halt()` requires a non-empty operator and reason, so the release is
  attributable — a latch a scheduler can clear on its own is not a latch.
- **Recovery from `DEGRADED_WARNING` has a hold-down** of
  `recovery_evaluations` consecutive healthy evaluations. Without it, sizing
  flaps 1.0 → 0.5 → 1.0 as a noisy estimate crosses back and forth, churning
  position size and transaction costs on noise.
- **Alerts are edge-triggered.** `alert_fn` fires on status transitions only.
  A channel that repeats itself every evaluation gets muted, and a muted
  channel misses the next incident. Exceptions raised by `alert_fn` are logged
  and swallowed: a broken pager must not take down the risk gate.

Evaluate on a cadence matched to the label horizon. Calling `evaluate_health()`
more often than the window turns over does not detect anything sooner; it only
inflates the consecutive-breach counter on substantially the same data.

### 6. Retraining and re-promotion

Retrain following `walk-forward-validation-setup` on the newly available data,
validate in a shadow/paper environment as for any new model, then call
`clear_halt()` with the operator and reason. Version the replacement per
`model-versioning-and-rollback` so the halt, the retrain and the promotion form
one auditable chain.

## Failure Modes Observed in Production

- **One-time deployment validation.** Treating deployment-time validation as
  permanent proof of reliability, ignoring regime shift.
- **Cumulative metric masking.** Tracking all-time accuracy, hiding recent
  decay behind past performance.
- **An empty window reported as perfect.** A restarted monitor with no history
  gives full size to a model it has never observed, and looks healthy doing it.
- **Halting on one breaching window.** A genuinely 55%-accurate model breaches a
  52% threshold on 34.7% of independent 60-observation windows (exact
  binomial). A gate that halts on the first breach halts a healthy model about a
  third of the time, and operators learn to override it.
- **A location statistic reported as PSI.** Blind to variance and shape change:
  a feature whose spread tripled with its mean unmoved scores zero drift.
- **Bounded PSI bins.** Discard exactly the live observations that have left the
  historical support.
- **Silent NaN.** Every `>=` comparison against NaN is `False`, so a naive
  monitor reports a NaN-poisoned feature as not drifting.
- **Unmonitored feature shift.** Ignoring input drift until the P&L notices.
- **Auto-retrain overwrites.** Retraining directly on live data without
  walk-forward validation, introducing look-ahead bias into the replacement.
- **Alert storms.** Re-alerting on every evaluation until the channel is muted.
