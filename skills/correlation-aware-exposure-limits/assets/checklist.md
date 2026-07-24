# Pre-Flight / Sign-off Checklist — correlation-aware-exposure-limits

Use this before considering the skill's implementation complete.

- [ ] **Correlation Estimation:** Confirm Pearson correlation matrix is computed over rolling 60-day windows.
- [ ] **Cluster Formation:** Confirm symbols with pairwise correlation $\ge 0.70$ are grouped into clusters.
- [ ] **Exposure Limit Enforcement:** Confirm `validate_order_exposure()` vetoes orders breaching 30% NAV cluster limit.
- [ ] **Automated Testing:** Run `python scripts/test_correlation_manager.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
