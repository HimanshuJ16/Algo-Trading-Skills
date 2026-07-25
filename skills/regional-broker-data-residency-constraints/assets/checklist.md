# Pre-Flight / Sign-off Checklist — regional-broker-data-residency-constraints

Use this before considering the skill's implementation complete.

- [ ] **Policy Registration:** Confirm broker jurisdiction and allowed AWS/GCP regions are registered.
- [ ] **Region Probing:** Confirm active cloud environment region is ingested accurately.
- [ ] **Data Residency Audit:** Confirm hosting region is verified against broker policy.
- [ ] **Compliance Veto:** Confirm non-compliant regions raise `DataResidencyViolationError`.
- [ ] **Automated Testing:** Run `python scripts/test_residency_guard.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
