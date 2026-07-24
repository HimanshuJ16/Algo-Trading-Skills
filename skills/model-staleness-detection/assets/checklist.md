# Pre-Flight / Sign-off Checklist — model-staleness-detection

Use this before considering the skill's implementation complete.

- [ ] **Rolling Metric Tracking:** Confirm accuracy/precision are calculated over trailing rolling windows ($N=60$), not cumulative all-time averages.
- [ ] **PSI Feature Drift Monitor:** Confirm feature drift is evaluated against training baselines via `compute_feature_drift()`.
- [ ] **Confidence Scaling Matrix:** Confirm `evaluate_health()` scales position sizing ($1.0 \rightarrow 0.5 \rightarrow 0.0$) on degradation.
- [ ] **Retraining & Shadow Testing:** Confirm model retraining follows walk-forward validation rules before live deployment.
- [ ] **Automated Testing:** Run `python scripts/test_staleness_monitor.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
