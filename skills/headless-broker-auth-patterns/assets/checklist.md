# Pre-Flight / Sign-off Checklist — headless-broker-auth-patterns

Use this before considering the skill's implementation complete.

## Archetype & permission gate

- [ ] **Archetype Confirmed:** Confirm from `references/standards.md` which archetype this broker actually publishes (C refresh-token / D static credential / E supervised gateway / A scripted login / B browser automation). Confirm no sanctioned C/D/E path exists before building A or B.
- [ ] **Terms of Use Checked:** For an Archetype A or B build, confirm the broker's terms permit automating its login surface. Zerodha states automating the Kite Connect login violates the API terms of use.
- [ ] **Indian Brokers — Compliance Boundary:** Confirm the design does not assume a session that spans trading days. NSE/INVG/67858 A.8 requires all API sessions to be logged out before the start of the next trading day; I.c mandates OAuth-based authentication and I.d mandatory 2FA.
- [ ] **Static IP Registered:** Confirm the deployment's egress IP is static and whitelisted with the broker (NSE/INVG/67858 A.1, I.e), and stable across restarts and instance replacement. Note it may be changed at most once a calendar week (A.6) — autoscaling and serverless runtimes will not work.

## Implementation

- [ ] **Refresh-Token Expiry Alarmed:** For Archetype C, confirm the refresh token's own expiry is tracked and alerts ahead of time (Fyers: 15 days), scheduling the interactive re-seed as planned maintenance rather than a market-open surprise.
- [ ] **TOTP Safety Window:** Confirm TOTP generation incorporates `TOTPHelper.get_totp_safe()` to avoid near-expiration turnover failures.
- [ ] **Checksum Signature Verification:** Confirm REST brokers (Fyers/Zerodha) calculate correct SHA-256 signatures via `ChecksumHelper` — for Fyers specifically, confirm `auth_code` is sent as a separate `code` field and is NOT included in the hash.
- [ ] **HTTP 200 Not Trusted:** Confirm token-exchange responses are unwrapped for a broker error body (`{"s": "error", "code": -371, ...}`) and raise `BrokerAuthError` naming the broker's own code/message, rather than surfacing as `KeyError`.
- [ ] **Redirect Token Parsed, Not Sliced:** Confirm Archetype B uses `extract_session_token()` (query-string parsing, case-insensitive, URL-decoded) and that the expected parameter name matches the broker's documentation — ICICI Breeze returns `API_Session`. Confirm a redirect missing the parameter raises rather than returning a truncated URL.
- [ ] **Headless Browser Cleanup:** Confirm browser automation uses `HeadlessBrowserContext` and leaves no orphaned Chrome processes (`ps aux | grep chrome`).
- [ ] **Bounded Element Waits:** Confirm `browser_login` uses `WebDriverWait` (not fixed `time.sleep()`) for every element interaction, with a sane `element_timeout_sec`.
- [ ] **Live-Probe Before Reuse:** Confirm token acquisition goes through `get_valid_session()` with a real `probe_fn` wired in (per `token-lifecycle-live-probing`) — not direct calls to the login functions that skip cache-probe-first logic.

## Token storage

- [ ] **Session-Date Cache Key:** Confirm `TokenCacheManager` is constructed with the broker's `session_tz` and `rollover_hour` (Kite: `Asia/Kolkata`, 6) rather than relying on the host's local midnight.
- [ ] **Token Cache File Permissions:** Confirm cache files under `.auth_cache/` are `0600` and the directory itself is `0700`. Note POSIX modes are not enforced on Windows — on Windows hosts, verify NTFS ACLs restrict the directory to the service account instead.
- [ ] **Stale Tokens Purged:** Confirm cache files from earlier sessions are removed (`purge_stale()` runs on save), so invalidated plaintext bearer tokens do not accumulate.
- [ ] **Secrets Segregated:** Confirm the refresh token, password, PIN and TOTP secret live in the secrets store, never in the token cache or a config file shared with non-sensitive settings.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s scripts` from the skill directory and confirm a 100% pass rate (27 tests as of this revision, including regression tests for the Fyers checksum fix, the Breeze `API_Session` redirect-extraction fix, the HTTP-200 error body, and the session-date rollover boundary).

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
