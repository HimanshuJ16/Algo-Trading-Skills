# Pre-Flight / Sign-off Checklist — questrade-api-rate-limit-and-account-types

Use this before considering the skill's implementation complete.

- [ ] **OAuth2 Refresh Token Rotation:** Confirm single-use refresh token is immediately persisted upon exchange.
- [ ] **Dynamic API Server Ingestion:** Confirm API calls target `auth_token.api_server` dynamically.
- [ ] **Token Bucket Rate Limiting:** Confirm requests are throttled to stay below 30 req/sec.
- [ ] **Registered Account Rules:** Confirm short selling is blocked on TFSA/RRSP/FHSA accounts.
- [ ] **Automated Testing:** Run `python scripts/test_questrade_client.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
