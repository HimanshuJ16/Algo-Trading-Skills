# Pre-Flight / Sign-off Checklist — walk-forward-validation-setup

Use this before considering the skill's implementation complete.

- [ ] **Chronological Ordering:** Confirm split generation never uses random shuffling on time-series data.
- [ ] **Embargo Gap Enforcement:** Confirm `embargo_indices` creates a gap $\ge \text{max\_feature\_lookback}$ between train and test windows.
- [ ] **Window Mode Selection:** Confirm choice of `EXPANDING` or `ROLLING` window is justified based on market regime assumptions.
- [ ] **Multi-Fold Performance Aggregation:** Confirm `evaluate_walk_forward()` outputs metrics across all individual folds.
- [ ] **Automated Testing:** Run `python scripts/test_walk_forward.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
