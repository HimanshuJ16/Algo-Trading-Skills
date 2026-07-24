# Pre-Flight / Sign-off Checklist — schwab-api-oauth-pkce-flow

Use this before considering the skill's implementation complete.

- [ ] **PKCE Generation:** Confirm `code_verifier` is 64 characters and `code_challenge` uses Base64URL without padding.
- [ ] **Auth URL Formatting:** Confirm authorization URL includes `code_challenge_method=S256`.
- [ ] **Preemptive Access Renewal:** Confirm access token is refreshed 300s prior to 30-minute expiry.
- [ ] **7-Day Refresh Alerting:** Confirm 24-hour warning alert triggers before 7-day refresh token expiration.
- [ ] **Automated Testing:** Run `python scripts/test_schwab_pkce_auth.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
