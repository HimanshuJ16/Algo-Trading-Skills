# Deep Workflow Reference — token-lifecycle-live-probing

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Read-Only Probe Execution:**
   - On bot startup, issue a designated low-cost GET read call (e.g. `/profile` or `/margins`) using cached token.
   - Never use order-related endpoints for probing to prevent unintended side effects.

2. **3-Outcome Classification:**
   - **VALID (2xx):** Cached token is working; proceed to trade.
   - **INVALID (401/403):** Token explicitly revoked; trigger headless re-authentication (`headless-broker-auth-patterns`).
   - **AMBIGUOUS (5xx / Timeout / Network Drop):** Retry with exponential backoff before deciding; do not trigger immediate re-authentication to avoid login rate limits.

3. **Re-Authentication & Post-Auth Verification:**
   - On `INVALID`, invoke `reauth_fn()`, then re-probe the freshly issued token before marking the bot ready to trade.

4. **Empirical Token Lifespan Tracking:**
   - Log observed token lifespans (`record_lifespan()`) over time to build empirical expiry baselines and proactively refresh tokens prior to expected invalidation windows.

## Failure Modes Observed in Production

- **Timestamp-Only Expiry Checks:** Relying on documented TTL timestamps without live probing, attempting trades with invalidated tokens during market open.
- **Side-Effect-Heavy Probing:** Using order placement or order modification calls for probing, risking accidental trade executions during health checks.
- **Aggressive Outage Re-Logins:** Treating single network timeouts as 401 invalidations, triggering login rate limits during broker API outages.

## Production Implementation Reference

- Reference code: `scripts/token_probe.py` (`LiveTokenProbeManager`, `ProbeOutcome`, `classify_probe_response`).
- Automated unit tests: `scripts/test_token_probe.py`.
