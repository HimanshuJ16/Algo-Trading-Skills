# Deep Workflow Reference — feature-engineering-without-leakage

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Target & Feature Cutoff Specification:**
   - Define prediction target timestamp explicitly (e.g. $Y_{t+1} = \text{sign}(\text{Close}_{t+1} - \text{Close}_t)$).
   - Require feature cutoff $t_{\text{feature}} \le t_{\text{prediction}}$.

2. **Automated Lead/Lag Cross-Correlation Audit:**
   - Run `FeatureLeakageAuditor.audit_dataframe()` to compute cross-correlations $\text{Corr}(X_t, Y_{t+k})$.
   - Flag any feature showing significant correlation ($r \ge 0.85$) with future target values ($k \ge 1$) as `FUTURE_LOOKAHEAD`.
   - Flag any feature with near-identical correlation ($r \approx 1.0$) at $t=0$ as `SAME_BAR_CONTAMINATION`.

3. **Point-In-Time As-Of Merge Verification:**
   - For multi-frequency datasets (e.g. joining daily fundamental/macro data with intraday price bars), enforce strict backward point-in-time merges via `point_in_time_asof_merge()`.
   - Ensure joined feature publication timestamps strictly precede trade timestamps.

4. **Shift Direction Verification:**
   - Validate pandas `.shift(periods)` direction: positive periods ($+k$) represent historical lags, negative periods ($-k$) represent future lookahead.

5. **Intentional Leakage Calibration Benchmark:**
   - Inject a known leaked feature (e.g. `df['target'].shift(-1)`) into the dataset using `run_intentional_leakage_calibration()`.
   - Confirm the auditor successfully catches the intentional leak with high confidence ($r \approx 1.0$).

## Failure Modes Observed in Production

- **Negative Shift Inversion:** Accidentally calling `.shift(-1)` instead of `.shift(1)` in feature pipelines, feeding future price moves into current model inputs.
- **Same-Bar High/Low Contamination:** Using bar $t$'s High/Low to predict bar $t$'s Close direction.
- **Calendar Date Joins:** Joining daily economic indicators by calendar date without verifying exact intraday release timestamps.
- **Unchecked High Accuracy:** Treating $\ge 95\%$ cross-validation accuracy in financial ML models as a sign of success rather than immediate proof of data leakage.

## Production Implementation Reference

- Reference code: `scripts/feature_audit.py` (`FeatureLeakageAuditor`, `LeakageFinding`, `LeakageType`, `verify_shift_direction`).
- Automated unit tests: `scripts/test_feature_audit.py`.
