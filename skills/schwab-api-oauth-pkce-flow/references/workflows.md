# Deep Workflow Reference — schwab-api-oauth-pkce-flow

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **RFC 7636 PKCE Pair Generation:**
   - Generate `code_verifier`: Cryptographically random 64-character string using `secrets.choice()`.
   - Calculate `code_challenge`: `Base64URL_NoPadding(SHA256(code_verifier))`.

2. **Authorization Request:**
   - Redirect user/operator to `https://api.schwabapi.com/v1/oauth/authorize` with `client_id`, `redirect_uri`, `code_challenge`, and `code_challenge_method=S256`.

3. **Token Exchange:**
   - POST authorization code + `code_verifier` to `https://api.schwabapi.com/v1/oauth/token` with HTTP Basic Auth headers (`Base64(app_key:app_secret)`).
   - Parse `access_token` (30m validity) and `refresh_token` (7-day validity).

4. **Preemptive Renewal & Expiry Monitoring:**
   - Refresh access token 5 minutes ($300\text{s}$) before 30-minute expiration.
   - Alert 24 hours prior to 7-day refresh token expiration to prompt operator re-authorization.

## Failure Modes Observed in Production

- **Padding Error in Code Challenge:** Retaining trailing `=` padding on Base64URL challenge string, rejected by Schwab API.
- **Mismatching Code Verifier:** Sending modified `code_verifier` during token POST, triggering `invalid_grant`.
- **Expired 7-Day Refresh Token:** Silent session termination on day 7 due to missing pre-expiry warning notifications.

## Production Implementation Reference

- Reference code: `scripts/schwab_pkce_auth.py` (`SchwabOAuthManager`, `SchwabPKCEGenerator`).
- Automated unit tests: `scripts/test_schwab_pkce_auth.py`.
