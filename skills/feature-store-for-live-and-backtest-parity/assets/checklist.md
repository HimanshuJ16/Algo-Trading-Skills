# Pre-Flight / Sign-off Checklist — feature-store-for-live-and-backtest-parity

Use this before considering the skill's implementation complete.

- [ ] **Single Point of Truth Feature Core:** Confirm `compute_features_from_window()` is consumed by both batch and online runtimes.
- [ ] **Batch Pipeline:** Confirm `compute_batch_features()` evaluates historical bar matrices.
- [ ] **Online Streaming Pipeline:** Confirm `compute_online_feature()` maintains a rolling ring buffer.
- [ ] **Parity Validation:** Confirm `validate_parity()` passes with max tolerance $\le 1\times 10^{-4}$.
- [ ] **Automated Testing:** Run `python scripts/test_feature_store.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
