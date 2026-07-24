# Pre-Flight / Sign-off Checklist — walk-forward-optimization-window-management

Use this before considering the skill's implementation complete.

- [ ] **Window Generation:** Confirm `generate_windows()` creates non-overlapping OOS intervals.
- [ ] **Temporal Isolation:** Confirm `validate_window_isolation()` passes for all generated slices.
- [ ] **WFE Calculation:** Confirm `calculate_wfe()` computes $\text{Sharpe}_{\text{OOS}} / \text{Sharpe}_{\text{IS}}$.
- [ ] **Robustness Thresholding:** Confirm $\text{WFE} \ge 0.50$ is enforced for production deployment sign-off.
- [ ] **Automated Testing:** Run `python scripts/test_walk_forward_manager.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
