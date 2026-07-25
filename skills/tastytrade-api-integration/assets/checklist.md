# Pre-Flight / Sign-off Checklist — tastytrade-api-integration

Use this before considering the skill's implementation complete.

- [ ] **Session Authentication:** Confirm `session-token` extracted from `/sessions` response.
- [ ] **OCC Symbol Format:** Confirm OCC option symbol matches 21-character specification (`AAPL  240816C00200000`).
- [ ] **Multi-Leg Option Payload:** Confirm legs array correctly maps actions (`Buy to Open`, `Sell to Close`).
- [ ] **Price Effect Specification:** Confirm net price and `price-effect` (`Credit` / `Debit`) match order intent.
- [ ] **Automated Testing:** Run `python scripts/test_tastytrade_client.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
