# Deep Workflow Reference — headless-broker-auth-patterns

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### Archetype A — REST-Based Headless Authentication (Fyers, Upstox, Alpaca, Zerodha)

1. **TOTP Window Safety Check:**
   - Generate TOTP code using `TOTPHelper.get_totp_safe()`.
   - Verify at least 5 seconds remain in the 30-second TOTP window before issuing HTTP request to prevent expiration in transit.

2. **SHA-256 Checksum Signature Generation:**
   - Generate SHA-256 signature using `ChecksumHelper` per broker specification.
   - **Fyers:** `appIdHash = sha256(f"{app_id}:{secret_key}")` — app_id and secret_key ONLY.
     `auth_code` is NOT part of the hash; it is sent as a separate `code` field in the
     token-exchange payload. Including it in the hash is a documented cause of Fyers'
     `-371` error ("Please provide sha256 hash of appId and app secret") and was a bug
     in an earlier version of this reference implementation — verified against Fyers'
     community-confirmed working implementations before correcting.
   - **Zerodha:** `checksum = sha256(api_key + request_token + api_secret)` — no
     separators. Verified directly against Zerodha's official `pykiteconnect` source.

3. **Daily Date-Keyed Token Caching with Live-Probe Reuse:**
   - Use `get_valid_session()` rather than calling `rest_login`/`browser_login` directly
     — it composes the full documented sequence: check `TokenCacheManager` → if a cached
     token exists, live-probe it (`probe_fn`, per the `token-lifecycle-live-probing`
     skill — a cheap, read-only, side-effect-free API call) → only call the
     archetype-appropriate login function if no cached token exists or the probe fails.
   - Never reuse a cached token without probing it first; a token surviving until the
     next date-keyed cache file doesn't mean the broker still considers it valid (see
     `token-lifecycle-live-probing`'s core premise — expiry cannot be assumed from a
     timestamp, only confirmed by a live call).

### Archetype B — Browser Automation Authentication (ICICI Breeze)

1. **Headless Browser Context Management:**
   - Wrap Selenium / Playwright instances in `HeadlessBrowserContext` to guarantee `driver.quit()` execution even on unhandled exceptions.
   - Prevent background Chrome zombie processes under systemd/cron restarts.

2. **Explicit WebDriver Element Waits (bounded timeout):**
   - `browser_login()` uses `WebDriverWait` with `expected_conditions` for every element
     interaction — never a fixed `time.sleep()`. Default per-element timeout is 15s,
     overridable via `element_timeout_sec`.
   - A slow page load or a login-page redesign that removes an expected element now
     fails fast with a clear `TimeoutException` inside the bounded window, rather than
     hanging the auth pipeline indefinitely or failing instantly on any load latency.
   - The final redirect wait polls for `session_token=`/`api_session=` appearing in
     `driver.current_url`, bounded by the same `WebDriverWait` timeout, instead of a
     fixed post-redirect sleep.

3. **Redirect Session Token Interception:**
   - Intercept redirect URL parameters (`session_token` / `api_session`) and immediately initialize broker SDK session objects.

## Failure Modes Observed in Production

- **Expired TOTP in Transit:** Submitting TOTP codes at 29s in the 30s window, causing intermittent authentication rejection.
- **Orphaned Chrome Zombies:** Failing to quit headless browser instances, exhausting host RAM under systemd auto-restarts.
- **Missing/Incorrect Checksum Signatures:** Omitting broker SHA-256 signature hashes, or computing them over the wrong fields (e.g. including `auth_code` in the Fyers hash), causing 401/`-371` API responses that don't obviously point back to the checksum as the cause.
- **Uncached Daily Re-Logins:** Triggering fresh headless logins on every bot restart, hitting broker login rate limits.
- **Trusting an Unprobed Cached Token:** Reusing a same-date cached token without a live probe, discovering it's actually invalid only when a real trading call fails — see `token-lifecycle-live-probing`.
- **Indefinite Selenium Hangs:** A `find_element` call with no explicit wait either fails instantly on normal page-load latency or, depending on driver configuration, hangs with no bound — both are worse than a bounded `WebDriverWait` failing predictably.

## Production Implementation Reference

- Reference code: `scripts/auth_probe.py` (`TOTPHelper`, `ChecksumHelper`, `HeadlessBrowserContext`, `TokenCacheManager`, `get_valid_session`).
- Automated unit tests: `scripts/test_auth_probe.py` — includes regression tests for both the Fyers checksum bug and the Archetype B WebDriverWait flow.
