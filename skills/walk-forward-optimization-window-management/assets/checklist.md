# Pre-Flight / Sign-off Checklist — walk-forward-optimization-window-management

Use this before considering the skill's implementation complete.

- [ ] **Embargo Sized From The Strategy:** Record the longest feature lookback `L` and longest
      label horizon `H`, and confirm `embargo_days >= max(L, H)` in calendar days. If
      `embargo_days = 0`, record why the strategy has neither.
- [ ] **Embargo Realised:** Confirm every slice has exactly `embargo_days` between `is_end` and
      `oos_start`, and that `[embargo_start, embargo_end]` is excluded from both training and
      scoring.
- [ ] **Window Generation:** Confirm `generate_windows()` produces the expected slice count, and
      that `step_days >= out_of_sample_days` (or that `allow_overlapping_oos=True` was set
      deliberately and the OOS curves are **not** concatenated).
- [ ] **Temporal Isolation:** Confirm `validate_window_isolation()` passes for all generated
      slices at the configured `min_embargo_days`.
- [ ] **Non-Overlapping OOS:** Confirm `validate_slice_sequence()` passes, and account for any
      untested gap it logs when `step_days > out_of_sample_days`.
- [ ] **Warm-Up Excluded:** Confirm `[warmup_start, is_start - 1]` bars are loaded for indicator
      state only and appear in no performance statistic, and that this history actually exists in
      the dataset.
- [ ] **Complete OOS Curve:** Confirm the stitched out-of-sample track record includes every
      slice, including losing ones — no cherry-picking.
- [ ] **Annualization Basis:** Confirm the IS and OOS Sharpe figures fed to `calculate_wfe()` are
      computed on the same annualization basis.
- [ ] **WFE Calculation:** Confirm `calculate_wfe()` computes
      $\text{Sharpe}_{\text{OOS}} / \text{Sharpe}_{\text{IS}}$ with no clamping of the
      denominator, and that slices returning a populated `undefined_reason` are treated as
      failures rather than passes.
- [ ] **Robustness Thresholding:** Confirm $\text{WFE} \ge 0.50$ is enforced for production
      deployment sign-off, and that it is recorded as a practitioner heuristic rather than a
      statistical guarantee.
- [ ] **Search Budget Recorded:** Record how many parameter combinations were evaluated per
      slice; WFE does not correct for selection across trials
      (see `walk-forward-hyperparameter-search-budget`).
- [ ] **Automated Testing:** Run `python -m unittest discover -s scripts` and confirm 100% test
      pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
