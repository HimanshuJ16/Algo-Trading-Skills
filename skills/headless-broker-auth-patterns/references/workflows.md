# Deep Workflow Reference — headless-broker-auth-patterns

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Archetype Selection (do this first)

`references/standards.md` records what each broker actually publishes. Reaching for a
scripted-login or browser-automation build when the broker offers a refresh token, static
keys, or a supervised gateway is the most expensive mistake in this area — and for Indian
brokers it also runs into the constraints in SKILL.md "When NOT to Use" (NSE/INVG/67858:
OAuth-only authentication, mandatory 2FA, compulsory daily session logout, static-IP
whitelisting).

Before writing an HTTP client, confirm the deployment's egress IP is static and
registered with the broker. Under NSE/INVG/67858 A.1/I.e the API key is bound to a
whitelisted static IP, and A.6 allows changing it at most once a calendar week — so an
autoscaling group or serverless runtime cannot authenticate at all, no matter how correct
the auth code is.

## Full Procedure

### Archetype C — Refresh-Token Exchange (Fyers; preferred where the broker offers it)

1. **Seed once, interactively.** Complete the OAuth login by hand and capture the refresh
   token alongside the access token. This step is irreducible — design the operational
   process around it instead of trying to script it away.
2. **Store the refresh token in the secrets store, not the token cache.** It outlives the
   daily access token and is correspondingly more valuable to an attacker.
3. **Exchange on each start** via `fyers_refresh_token_login()`: POST
   `{grant_type: "refresh_token", appIdHash: sha256(f"{app_id}:{secret_key}"),
   refresh_token, pin}` to `validate-refresh-token`. The same `appIdHash` rule as the
   auth-code exchange applies.
4. **Alarm ahead of the refresh token's own expiry.** Fyers' refresh token is valid 15
   days (Fyers support KB). Track its issue date and raise a maintenance alert several
   days before it lapses; discovering it during pre-open is precisely the failure this
   skill exists to prevent.

### Archetype A — Scripted Credential/TOTP Login (only where permitted)

1. **TOTP Window Safety Check:**
   - Generate TOTP code using `TOTPHelper.get_totp_safe()`.
   - Verify at least 5 seconds remain in the 30-second TOTP window before issuing HTTP request to prevent expiration in transit.

2. **Treat HTTP 200 as inconclusive:**
   - These APIs return failures in the response *body*, not the status line. Fyers returns
     `{"s": "error", "code": -371, "message": "Please provide sha256 hash of appId and app
     secret"}` with HTTP 200, so `raise_for_status()` passes and the caller gets
     `KeyError: 'access_token'` — an error that points nowhere near the checksum that
     caused it.
   - `_require_json_field()` unwraps the payload and surfaces the broker's own
     `s`/`code`/`message` in the raised `BrokerAuthError`.

3. **SHA-256 Checksum Signature Generation:**
   - Generate SHA-256 signature using `ChecksumHelper` per broker specification.
   - **Fyers:** `appIdHash = sha256(f"{app_id}:{secret_key}")` — app_id and secret_key ONLY.
     `auth_code` is NOT part of the hash; it is sent as a separate `code` field in the
     token-exchange payload. Including it in the hash is a documented cause of Fyers'
     `-371` error ("Please provide sha256 hash of appId and app secret") and was a bug
     in an earlier version of this reference implementation — verified against Fyers'
     community-confirmed working implementations before correcting.
   - **Zerodha:** `checksum = sha256(api_key + request_token + api_secret)` — no
     separators. Verified directly against Zerodha's official `pykiteconnect` source.

4. **Session-Date-Keyed Token Caching with Live-Probe Reuse:**
   - Use `get_valid_session()` rather than calling `rest_login`/`browser_login` directly
     — it composes the full documented sequence: check `TokenCacheManager` → if a cached
     token exists, live-probe it (`probe_fn`, per the `token-lifecycle-live-probing`
     skill — a cheap, read-only, side-effect-free API call) → only call the
     archetype-appropriate login function if no cached token exists or the probe fails.
   - Never reuse a cached token without probing it first; a token surviving until the
     next date-keyed cache file doesn't mean the broker still considers it valid (see
     `token-lifecycle-live-probing`'s core premise — expiry cannot be assumed from a
     timestamp, only confirmed by a live call).
   - **Key on the broker's session date, not the host's local date.** Kite Connect access
     tokens expire at 06:00 IST the following day (Kite Connect v3 docs, described there
     as a regulatory requirement), and NSE/INVG/67858 A.8 forces a daily logout. Construct
     the cache as `TokenCacheManager(session_tz=ZoneInfo("Asia/Kolkata"), rollover_hour=6)`.
     With the naive default, a UTC-hosted bot keeps reusing a flushed token for hours,
     while an IST-hosted bot re-authenticates needlessly between midnight and 06:00 — and
     for an interactive-login broker, a needless re-login means waking a human.
   - `save_token()` purges cache files from earlier sessions. Yesterday's plaintext bearer
     token has already been invalidated by the mandatory daily logout, so keeping it on
     disk is leak surface with no operational value.

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
   - Parse the redirect's query string with `extract_session_token()`; do **not** slice the
     URL on a literal `"session_token="`. ICICI Breeze returns the token as `API_Session`
     (that capitalisation, per ICICI Direct's own documentation), so a slice on
     `"session_token="` finds no separator and returns the entire URL prefix as the
     "token" — silently, with no exception. The redirect wait now polls on *"is the token
     extractable?"* rather than on a substring, so the wait and the extraction cannot
     disagree about what counts as a successful redirect.
   - Matching is case-insensitive and values are URL-decoded; a redirect carrying none of
     the expected parameters raises `BrokerAuthError` naming the parameters that were
     present, so a login-page redesign fails at the point of breakage.
   - Immediately exchange the raw token for the broker SDK session object (Breeze:
     `generate_session(api_secret, session_token)`).

## Known Failure Modes

- **Expired TOTP in Transit:** Submitting TOTP codes at 29s in the 30s window, causing intermittent authentication rejection.
- **Orphaned Chrome Zombies:** Failing to quit headless browser instances, exhausting host RAM under systemd auto-restarts.
- **Missing/Incorrect Checksum Signatures:** Omitting broker SHA-256 signature hashes, or computing them over the wrong fields (e.g. including `auth_code` in the Fyers hash), causing 401/`-371` API responses that don't obviously point back to the checksum as the cause.
- **Redirect Token Extracted by String Slicing:** Against a broker whose parameter is not literally `session_token` — ICICI Breeze uses `API_Session` — slicing returns the whole URL prefix as the token and the integration fails later, far from the cause.
- **Cache Keyed on Local Midnight:** Reusing a token the broker flushed at 06:00 IST, or forcing a re-login in the 00:00–06:00 window when the token was still perfectly valid.
- **Ephemeral Deployment IP:** Autoscaling or serverless hosts fail the broker's static-IP whitelist on every new instance, and the IP mapping can only be changed once a calendar week.
- **Stale Token Files Accumulating:** Date-keyed cache files from prior sessions holding plaintext bearer tokens that the mandatory daily logout already invalidated.
- **Uncached Daily Re-Logins:** Triggering fresh headless logins on every bot restart, hitting broker login rate limits.
- **Trusting an Unprobed Cached Token:** Reusing a same-date cached token without a live probe, discovering it's actually invalid only when a real trading call fails — see `token-lifecycle-live-probing`.
- **Indefinite Selenium Hangs:** A `find_element` call with no explicit wait either fails instantly on normal page-load latency or, depending on driver configuration, hangs with no bound — both are worse than a bounded `WebDriverWait` failing predictably.

## Production Implementation Reference

- Reference code: `scripts/auth_probe.py` (`TOTPHelper`, `ChecksumHelper`, `HeadlessBrowserContext`, `TokenCacheManager`, `extract_session_token`, `fyers_refresh_token_login`, `get_valid_session`, `BrokerAuthError`).
- Automated unit tests: `scripts/test_auth_probe.py` — includes regression tests for the Fyers checksum bug, the `API_Session` redirect-extraction bug, the HTTP-200-with-error-body case, and the session-date cache boundary.

`rest_login()` remains an illustrative two-step template rather than a literal Fyers
client — the real flow involves additional client-id/TOTP/PIN verification steps. The
checksum helpers and the refresh-token exchange are the parts that mirror documented
broker behaviour exactly; adapt the request shape to the broker's current documented
endpoints.
