# Pre-Flight / Sign-off Checklist — broker-failover-secondary-account-routing

Use this before considering the skill's implementation complete.

- [ ] **Primary & Secondary Registration:** Confirm primary and backup adapters are initialized.
- [ ] **Symbol Translation Mapping:** Confirm canonical symbol mapping resolves correctly per broker.
- [ ] **Circuit Breaker Tripping:** Confirm 3 consecutive failures mark primary broker as `DOWN`.
- [ ] **Automatic Rerouting:** Confirm subsequent orders dispatch to secondary broker seamlessly.
- [ ] **Automated Testing:** Run `python scripts/test_failover_router.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
