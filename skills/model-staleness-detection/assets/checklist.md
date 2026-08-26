# Pre-Flight / Sign-off Checklist — model-staleness-detection

Use this before considering the skill's implementation complete.

## Data capture

- [ ] **All inferences logged:** every live prediction is logged with its feature snapshot and eventual realised outcome — not only the ones that became trades.
- [ ] **Outcomes fully realised:** an outcome is recorded only once its horizon has completed. No prediction is scored against a partially formed bar.
- [ ] **Discrete labels:** continuous model output is bucketed into labels where the thresholds are visible; `record_prediction()` is never fed floats.
- [ ] **Window survives restarts:** `restore_history()` reloads the rolling window from the durable prediction log on start-up.

## Metrics

- [ ] **Rolling, not cumulative:** accuracy/precision are computed over the trailing window, never as an all-time average.
- [ ] **Window sized to the horizon:** `window` counts realised outcomes, not calendar days, and was chosen against the model's label horizon and inference rate.
- [ ] **Sample floor enforced:** below `min_predictions`, the status is `INSUFFICIENT_DATA` and `get_rolling_accuracy()` returns `None` — never a default that reads as healthy.
- [ ] **Confidence bound surfaced:** `accuracy_lower_bound` is shown next to the point estimate wherever the point estimate is shown.
- [ ] **Precision tracked** on the traded side where the strategy acts on one direction.

## Feature drift

- [ ] **Reference sample registered** (`{"reference_sample": [...]}`) rather than mean/std, so drift is real binned PSI and not a location-only proxy.
- [ ] **Method surfaced:** `FeatureDriftResult.method` is visible on the dashboard, so a `GAUSSIAN_JEFFREYS` fallback is not mistaken for full PSI.
- [ ] **Max, not mean:** the halt trigger reads `max_psi` / `max_psi_feature`; the drifting-feature count never decides on its own.
- [ ] **Unmeasurable is not clean:** `NON_FINITE`, `NO_BASELINE`, `NO_LIVE_DATA`, `INSUFFICIENT_LIVE_DATA`, `DEGENERATE_BASELINE` and `MISSING_FROM_BATCH` each raise the health state and are named in the alert. None reports `psi_score = 0.0`.
- [ ] **Baseline and batch agree:** every feature in the live batch resolves to a registered baseline, and every registered baseline appears in the batch (no `NO_BASELINE` and no `MISSING_FROM_BATCH` in a clean run).

## Thresholds and response

- [ ] **Thresholds committed in advance**, recorded with a date and an owner, and not revised while looking at a suspicious pattern.
- [ ] **Sustained-breach requirement set:** `consecutive_breaches_to_halt` > 1, with the false-halt arithmetic in `references/standards.md` reviewed against the chosen window.
- [ ] **Sizing matrix wired:** `evaluate_health()` drives `1.0 → 0.5 → 0.0` (and `warmup_sizing_multiplier`) into the actual order sizing path.
- [ ] **Halt wired to the kill switch:** `HALTED_STALE` reaches the mechanism in `kill-switch-and-drawdown-circuit-breakers`; this monitor does not cancel or flatten on its own.
- [ ] **Halt latches** and is cleared only by an attributed `clear_halt(operator, reason)`; the record is retained.
- [ ] **Alerts are edge-triggered** and reach a channel that is actually monitored; a failing `alert_fn` does not take down the risk gate.
- [ ] **Evaluation cadence** is matched to the label horizon, not to the tick rate.

## Retraining

- [ ] **Walk-forward discipline:** retraining follows `walk-forward-validation-setup`; no fitting on the live window that triggered the alert without re-validation.
- [ ] **Shadow period:** the retrained model runs shadow/paper validation before replacing the live one, and is versioned per `model-versioning-and-rollback`.

## Testing

- [ ] **Unit suite green:** `python -m unittest discover -s skills/model-staleness-detection/scripts` reports 51 passing tests.
- [ ] **Replay test:** replaying the production prediction log through the monitor reproduces the metric the dashboard displayed.
- [ ] **Threshold logic tested offline** — the halt path is exercised without live trading.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Thresholds committed (accuracy / PSI warning / PSI halt / consecutive breaches): ___________________________
