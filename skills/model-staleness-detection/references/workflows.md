# Deep Workflow Reference — model-staleness-detection

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Logging Predictions & Outbound Realized Labels:**
   - Record every live inference (prediction, confidence score, feature snapshot) alongside actual market outcome.

2. **Rolling Accuracy & Precision Metrics:**
   - Compute trailing rolling window metrics ($N=60$ days) via `ModelStalenessMonitor.get_rolling_accuracy()`. Never rely on cumulative all-time averages.

3. **Population Stability Index (PSI) & Feature Drift Inspection:**
   - Compare live feature distributions against training baseline stats using `compute_feature_drift()`.
   - Track Z-score distance and PSI estimates to catch feature drift before accuracy decay occurs.

4. **Automated Position Sizing Scaling Matrix:**
   - `HEALTHY`: $1.0\times$ position sizing multiplier.
   - `DEGRADED_WARNING`: $0.5\times$ position sizing multiplier.
   - `HALTED_STALE`: $0.0\times$ multiplier; halt signal generation and trigger retraining workflow.

5. **Retraining & Walk-Forward Validation:**
   - Retrain degrading models following `walk-forward-validation-setup` and validate on a shadow paper trading environment before re-promoting to live execution.

## Failure Modes Observed in Production

- **One-Time Deployment Validation:** Treating deployment-time validation as permanent proof of reliability, ignoring market regime shifts.
- **Cumulative Metric Masking:** Tracking cumulative all-time accuracy, masking recent model decay with past performance.
- **Unmonitored Feature Shift:** Ignoring feature distribution shifts until after significant trading capital losses occur.
- **Auto-Retrain Overwrites:** Retraining models directly on live data without walk-forward validation, introducing lookahead bias.

## Production Implementation Reference

- Reference code: `scripts/staleness_monitor.py` (`ModelStalenessMonitor`, `ModelHealthStatus`, `FeatureDriftResult`, `ModelStalenessReport`).
- Automated unit tests: `scripts/test_staleness_monitor.py`.
