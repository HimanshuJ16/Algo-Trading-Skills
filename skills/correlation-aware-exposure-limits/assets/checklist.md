# Pre-Flight / Sign-off Checklist — correlation-aware-exposure-limits

Use this before considering the skill's implementation complete.

- [ ] **Correlation Estimation:** Confirm Pearson correlation matrix is computed over rolling 60-day windows, on validated (positive, finite, chronological, recent-aligned) price history.
- [ ] **Cluster Formation:** Confirm symbols with pairwise correlation $\ge 0.70$ are grouped into transitive connected-component clusters; sector-mapped symbols share a cluster.
- [ ] **Fail-Closed Gates:** Confirm `evaluate_proposed_position()` raises `CorrelationMatrixUnavailableError` before any matrix exists, and that stale-matrix policy is set to `block` for production.
- [ ] **Exposure Limit Enforcement:** Confirm exposure-increasing orders breaching the cluster notional cap are vetoed (`RiskCheckResult(approved=False)`), while risk-reducing orders are approved and flagged for remediation.
- [ ] **Delta Symmetry:** Confirm options delta weights apply to existing positions and proposed orders alike.
- [ ] **Audit Trail:** Confirm every decision is written to `audit_trail` before the caller sees the result.
- [ ] **Automated Testing:** Run `python scripts/test_exposure_limits.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
