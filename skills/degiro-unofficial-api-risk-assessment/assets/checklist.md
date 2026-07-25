# Pre-Flight / Sign-off Checklist — degiro-unofficial-api-risk-assessment

Use this before considering the skill's implementation complete.

- [ ] **Session Authentication & Token Extraction:** Confirm `sessionId` and `intAccount` extracted correctly.
- [ ] **Risk Score Evaluator:** Confirm login attempt frequency and session age increase risk score.
- [ ] **Pre-Trade Dry-Run (`checkOrder`):** Confirm transaction fee estimation and confirmation ID returned.
- [ ] **Order Circuit Breaker:** Confirm orders are blocked when risk score exceeds 0.70 threshold.
- [ ] **Automated Testing:** Run `python scripts/test_degiro_client.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
