# Pre-Flight / Sign-off Checklist — multi-region-active-active-tick-ingestion

Use this before considering the skill's implementation complete.

- [ ] **Dual-Region Stream Setup:** Confirm parallel feeds from two distinct regions are ingested.
- [ ] **Deterministic Signature Hashing:** Confirm signature hashing matches across regions.
- [ ] **First-Arrival Selection:** Confirm earliest-arriving tick is forwarded to trading engine.
- [ ] **Duplicate Filtering:** Confirm late-arriving twin ticks are dropped with latency metrics recorded.
- [ ] **Automated Testing:** Run `python scripts/test_active_active_ingest.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
