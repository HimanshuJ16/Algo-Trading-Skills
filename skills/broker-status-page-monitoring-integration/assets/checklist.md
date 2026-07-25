# Pre-Flight / Sign-off Checklist — broker-status-page-monitoring-integration

Use this before considering the skill's implementation complete.

- [ ] **Status Feed Registration:** Confirm Statuspage.io endpoints registered per broker.
- [ ] **Indicator Parsing:** Confirm `none`, `minor`, and `critical` indicators map to platform states cleanly.
- [ ] **Outage vs Bug Diagnosis:** Confirm execution exceptions during active outages classify as `EXTERNAL_BROKER_OUTAGE`.
- [ ] **Automated Testing:** Run `python scripts/test_status_monitor.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
