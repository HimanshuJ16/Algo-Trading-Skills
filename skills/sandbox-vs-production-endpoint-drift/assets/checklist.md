# Pre-Flight / Sign-off Checklist — sandbox-vs-production-endpoint-drift

Use this before considering the skill's implementation complete.

- [ ] **Payload Schema Audit:** Confirm `compare_schemas()` checks for missing fields and type mismatches across environments.
- [ ] **Header Rate-Limit Audit:** Confirm rate-limit headers exist and format matches between sandbox and prod.
- [ ] **Status Code Behavior:** Confirm error status codes match for invalid requests.
- [ ] **Promotion Gate Sign-off:** Confirm zero `CRITICAL` drift findings exist before live promotion.
- [ ] **Automated Testing:** Run `python scripts/test_drift_detector.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
