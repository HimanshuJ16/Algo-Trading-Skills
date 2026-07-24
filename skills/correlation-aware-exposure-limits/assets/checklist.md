# Pre-Flight / Sign-off Checklist — correlation-aware-exposure-limits

Use this before considering the skill's implementation complete.

- [ ] **Cluster Cap Enforcement:** Confirm that proposed positions in highly correlated assets (e.g. multiple bank stocks) trigger cluster limit checks and scale down allowed notional even if individual position limits pass.
- [ ] **Rolling Matrix Refresh:** Confirm correlation matrix computation timestamp is validated against staleness thresholds (`max_matrix_age_days`).
- [ ] **Options Delta Aggregation:** Confirm options contracts are weighted by underlying delta factor ($\Delta$) when aggregating cluster exposures.
- [ ] **Audit Trail Logging:** Confirm all risk evaluations log structured entries containing timestamps, symbol, cluster ID, and decision rationale.
- [ ] **Automated Testing:** Run `python scripts/test_exposure_limits.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
