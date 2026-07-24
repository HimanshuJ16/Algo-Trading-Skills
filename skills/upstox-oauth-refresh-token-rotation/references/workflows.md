# Deep Workflow Reference — upstox-oauth-refresh-token-rotation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Preemptive Expiry Inspection:**
   - Inspect local token state before issuing trading requests.
   - Trigger rotation if $T_{\text{now}} \ge T_{\text{expires}} - 900\text{s}$ (15-minute buffer).

2. **Thread-Safe Lock Acquisition:**
   - Acquire `threading.Lock()` or `asyncio.Lock()` to prevent multi-threaded workers from issuing simultaneous refresh requests with the same single-use refresh token.

3. **HTTP Refresh Exchange:**
   - Submit POST request to `https://api.upstox.com/v2/login/auth/token` with `grant_type=refresh_token`.
   - Parse `access_token`, `refresh_token`, and `expires_in` from JSON response.

4. **Atomic Token Persistence:**
   - Write updated `UpstoxTokenState` to temporary file (`.tmp`) and perform atomic rename onto persistent token storage (`upstox_tokens.json`).

5. **Revocation Fallback Handler:**
   - On `invalid_grant` or HTTP 401 response, trigger emergency re-authentication fallback callback (`headless-broker-auth-patterns`).

## Failure Modes Observed in Production

- **Double-Using Rotated Refresh Tokens:** Re-issuing a refresh POST with an already-invalidated refresh token, invalidating all session credentials.
- **Race Conditions Across Threads:** Multiple concurrent strategy processes attempting simultaneous token refreshes.
- **In-Memory Token Data Loss:** Storing rotated refresh tokens in RAM only, causing session death on process restart.

## Production Implementation Reference

- Reference code: `scripts/upstox_auth.py` (`UpstoxTokenManager`, `UpstoxTokenState`).
- Automated unit tests: `scripts/test_upstox_auth.py`.
