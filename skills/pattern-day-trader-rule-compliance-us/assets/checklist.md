# Pre-Flight / Sign-off Checklist — pattern-day-trader-rule-compliance-us

Use this before considering the skill's implementation complete.

- [ ] **Rolling Business-Day Tracking:** Confirm `PDTComplianceEngine` tracks day trades over a 5-business-day window (excluding weekends).
- [ ] **Sub-$25k Equity Veto:** Confirm 4th day trade is blocked via `would_breach_pdt()` when account equity is under $25,000.
- [ ] **Equity Threshold Exemption:** Confirm accounts with equity $\ge \$25,000$ are permitted to execute day trades without veto.
- [ ] **Broker Count Reconciliation:** Confirm local day-trade counts match broker API day-trade counters via `reconcile_broker_count()`.
- [ ] **Automated Testing:** Run `python scripts/test_pdt_tracker.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
