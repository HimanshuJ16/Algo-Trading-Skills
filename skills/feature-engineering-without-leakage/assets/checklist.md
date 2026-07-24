# Pre-Flight / Sign-off Checklist — feature-engineering-without-leakage

Use this before considering the skill's implementation complete.

- [ ] **Lead/Lag Correlation Audit:** Run `FeatureLeakageAuditor` and confirm zero features correlate with future target leads ($k \ge 1$).
- [ ] **Same-Bar Contamination Verification:** Confirm no feature relies on same-bar unclosed High/Low/Close data.
- [ ] **Point-In-Time As-Of Merge Check:** Confirm multi-frequency datasets are merged using `point_in_time_asof_merge(direction='backward')`.
- [ ] **Shift Direction Audit:** Confirm all pandas `.shift()` calls use positive lag periods.
- [ ] **Intentional Leakage Calibration:** Run `run_intentional_leakage_calibration()` to confirm auditor sensitivity.
- [ ] **Automated Testing:** Run `python scripts/test_feature_audit.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
