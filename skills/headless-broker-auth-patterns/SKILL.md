---
name: headless-broker-auth-patterns
description: Use when integrating a new broker (Fyers, Zerodha Kite, ICICI Breeze,
  Upstox, Alpaca, IBKR) that requires unattended/headless login for a bot that runs
  without a human clicking through an OAuth screen daily
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- fyers-api-v3
- zerodha-kite-connect
- icici-breeze-api
brokers_frameworks:
- Fyers API v3
- Zerodha Kite Connect
- ICICI Breeze API
- Upstox API v2
- Alpaca Trading API
- IBKR TWS/Gateway API
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a bot needs to authenticate with a broker without a human present at bot-start time — nightly restarts, systemd auto-restart, or cloud deployment where no browser session exists.

**Start by identifying which archetype the broker actually is.** The single most expensive mistake in this area is building against a mechanism the broker does not publish. There are five, not two:

| Archetype | Mechanism | Brokers | Unattended? |
|---|---|---|---|
| **C — Refresh token** | One interactive OAuth login seeds a longer-lived refresh token; the bot exchanges it for daily access tokens | Fyers API v3 (refresh token valid 15 days) | Yes, until the refresh token expires — **prefer this wherever it exists** |
| **D — Static credential** | API key + secret sent as headers on every request; no login, no session, no expiry | Alpaca Trading API | Yes, trivially. The problem is key custody, not session acquisition |
| **E — Supervised gateway** | A long-running local gateway process holds the session; 2FA approved out-of-band | IBKR TWS/IB Gateway (via IBC / IBAutomater) | Partly — IBKR forces a periodic restart, and 2FA arrives as a push to IBKR Mobile |
| **A — Scripted credential/TOTP post** | Credentials + TOTP posted to a login endpoint that returns an auth code | Fyers/Zerodha *unofficial* internal endpoints | Technically yes; see **When NOT to Use** first |
| **B — Browser automation** | Drive the broker's human login page and intercept the redirect | ICICI Breeze (publishes no session-creation API at all) | Technically yes; see **When NOT to Use** first |

Archetypes C, D and E are broker-sanctioned. A and B are not: they automate a login surface the broker built for a human, and both are covered by the constraints below. Reach for A or B only after confirming C/D/E genuinely do not exist for that broker, and only where you have established that automating it is permitted.

## When NOT to Use

Do not use the Archetype A or B paths to build indefinitely-unattended authentication against an Indian broker (Fyers, Zerodha Kite, ICICI Breeze, Upstox). This is a stated constraint, not a stylistic preference:

- **Zerodha states it directly.** On automating the Kite Connect login: "this was never allowed to begin with. If you were doing it, you were in violation of the terms of use of the APIs." Kite's own docs note the access token expires at 6 AM the next day as a *regulatory* requirement.
- **The mandated mechanism is OAuth.** NSE circular NSE/INVG/67858 (05-May-2025), Annexure para I.c, requires brokers to have "OAuth (Open Authentication) based authentication only or any authentication mechanism allowed / communicated by the Exchange / SEBI from time to time," and para I.d requires client API access to be authenticated "through two factor authentication." A scripted credential+TOTP post to an undocumented internal endpoint is not that mechanism.
- **Sessions cannot span days.** Annexure para A.8: "All API sessions shall be compulsorily logged out every day before the start of the next trading day." A design premised on a session that survives indefinitely is not achievable compliantly.
- **Upstox publishes no such endpoint.** Its documentation states plainly: "There is no public endpoint for other applications to directly log the customer into their upstox.com." Any library offering headless Upstox TOTP login is driving a surface Upstox does not support.

These standards are fully applicable to all stock brokers from 01-Apr-2026 (SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132, 30-Sep-2025, extending SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, 04-Feb-2025). Outside India, and for brokers whose terms permit it, Archetypes A and B remain legitimate — the mechanics below are correct and worth implementing well. Confirm the broker's own terms of use before automating a login page; this repository is not legal or compliance advice.

## Prerequisites

- Broker developer app registered (API key + secret, redirect URI configured)
- **A static IP registered with the broker, for Indian brokers.** NSE/INVG/67858 A.1 makes this mandatory for API access, I.e requires access "only through a unique vendor client specific API key and static IP whitelisted by the broker," and A.6 permits changing the mapped IP "not more than once a calendar week." A headless bot on an ephemeral cloud IP simply will not authenticate, and you cannot chase a rotating address by re-registering daily. Budget for a static/elastic IP or a fixed-IP NAT gateway before writing any auth code.
- Confirmation of which archetype the broker is (table above) — before writing an HTTP client against an endpoint that may not exist
- TOTP secret provisioned for 2FA-enabled accounts, only where the scripted path is permitted (store as encrypted secret, never plaintext in repo)
- For Archetype B: headless Chromium/Chrome available in the deployment environment (not just dev machine — this breaks silently in minimal Docker images missing browser deps)
- A secrets store (env vars, Vault, or at minimum a `.env` excluded from git) — credentials must never be hardcoded

- Broker developer app registered (API key + secret, redirect URI configured)
- TOTP secret provisioned for 2FA-enabled accounts (store as encrypted secret, never plaintext in repo)
- For Archetype B: headless Chromium/Chrome available in the deployment environment (not just dev machine — this breaks silently in minimal Docker images missing browser deps)
- A secrets store (env vars, Vault, or at minimum a `.env` excluded from git) — credentials must never be hardcoded

## Workflow

### Archetype C — Refresh token (preferred where it exists, e.g. Fyers)
1. Complete the interactive OAuth login **once**, by hand, and capture both the access token and the refresh token. This is the step that cannot be eliminated; design around it rather than trying to script it away.
2. Store the refresh token in the secrets store, not the token cache — it is a longer-lived credential of higher value than the daily access token.
3. On each bot start, POST the documented refresh exchange (`fyers_refresh_token_login`): `grant_type=refresh_token`, `appIdHash`, `refresh_token`, `pin`. No browser, no stored password.
4. **Alarm on the refresh token's own expiry before it bites.** Fyers' refresh token is valid 15 days; when it lapses a human must redo step 1. Schedule that as planned maintenance on a non-trading day — discovering it at 09:10 IST is the whole failure mode this skill exists to prevent.

### Archetype A (Scripted credential/TOTP post) — only where permitted, see When NOT to Use
1. Generate TOTP code from the stored secret using a standard TOTP library (`pyotp` or equivalent) — do not assume the code is valid for more than ~30s, generate it at the moment of use.
2. POST credentials + TOTP to the broker's login-step endpoint to obtain an intermediate auth code.
3. Exchange the auth code for an access token via the token endpoint. The checksum shape is broker-specific and getting it wrong produces errors that do not name the checksum — Fyers uses `sha256(app_id + ":" + secret_key)` with the auth code sent separately as `code`; Zerodha uses `sha256(api_key + request_token + api_secret)` with no separators. Never guess this shape from another broker's.
4. Treat an HTTP 200 as inconclusive. These APIs return errors in the *body* (`{"s": "error", "code": -371, ...}`), so `raise_for_status()` passing means nothing. Unwrap the payload and surface the broker's own code and message, or the failure resurfaces later as an opaque `KeyError`.
5. Persist the resulting access token keyed by the **broker's session date**, not the host's local calendar date — see Common Pitfalls.
6. On subsequent bot starts in the same session, attempt to reuse the cached token via a **live probe** (see `token-lifecycle-live-probing` skill) before triggering a fresh login — do not blindly re-login every restart, as some brokers rate-limit login attempts.

### Archetype B (Browser automation, e.g. Breeze) — only where permitted, see When NOT to Use
1. Launch Selenium/Playwright in headless mode with a persistent user-data-dir if the broker sets long-lived cookies that reduce friction on subsequent logins.
2. Navigate to the broker's actual login URL (same one a human would use) — do not try to reverse-engineer a "hidden" API endpoint; ICICI Breeze does not have a session-creation API and treating scraped internal endpoints as stable is a common cause of silent breakage when the broker changes their frontend.
3. Fill username/password fields using explicit waits on element visibility, not fixed `sleep()` calls — page load time varies and fixed sleeps cause flaky failures under CI/cron load.
4. Handle 2FA: if TOTP-based, generate and submit the same way as Archetype A; if SMS/email OTP based, this cannot be fully automated — flag this to the user as requiring manual intervention or a semi-automated fallback (e.g., a webhook that receives the OTP from an email parser), and document this limitation explicitly rather than pretending full automation is possible.
5. After successful login, intercept the redirect URL (Selenium's `driver.current_url` after redirect, or a network-request listener) and **parse the query string properly** to extract the token parameter. Confirm the exact parameter name against the broker's documentation — ICICI Breeze returns `API_Session`, with that capitalisation. Do not slice the URL on a literal `"session_token="`: when the parameter is named anything else, the slice silently yields the whole URL prefix and hands it downstream as though it were a token.
6. Immediately exchange this raw session token for the broker SDK's working session object (e.g., `breeze.generate_session(api_secret, session_token)`), then close/quit the browser instance — do not leave headless Chrome processes running, they accumulate as zombie processes under systemd restarts and exhaust memory over days.
7. Cache the resulting session identifier the same way as Archetype A.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Building an Archetype A/B integration for a broker that has a sanctioned Archetype C/D/E path. Fyers publishes a 15-day refresh token; scripting its login page instead trades a supported mechanism for an unsupported one and gains nothing.
- Extracting the redirect token by string-slicing on `"session_token="`. Against ICICI Breeze — whose parameter is `API_Session` — the slice matches no separator and returns the entire URL prefix as the "token", with no exception raised. Parse the query string and fail loudly when the expected parameter is absent.
- Keying the token cache on the host's local calendar date. Kite Connect tokens expire at 6 AM IST, not at local midnight: a UTC-hosted bot keeps reusing a token the broker already flushed, while an IST-hosted bot re-logs in needlessly between 00:00 and 06:00 — and for an interactive-login broker, a needless re-login means waking a human.
- Deploying to an autoscaling group, serverless runtime, or any host with a rotating egress IP. Under the Indian framework the API key is bound to a whitelisted static IP that may be changed at most once a calendar week, so the bot fails auth on every new instance and cannot re-register its way out.
- Treating HTTP 200 as success. Fyers returns `-371` ("Please provide sha256 hash of appId and app secret") inside a 200 body; the resulting `KeyError: 'access_token'` points nowhere near the checksum that actually caused it.
- Assuming a browser-driven login is a one-time setup cost — it must run on every session-token expiry, so it needs to be as reliable as the REST path, not a manual fallback script.
- Not handling headless Chrome crashes/hangs with a timeout wrapper — a hung Selenium session blocks the entire auth pipeline and, if auth runs synchronously before market open, delays the whole bot.
- Storing the TOTP secret or password in the same config file as non-sensitive settings, making it easy to accidentally commit.
- Forgetting that headless mode can behave differently from headed mode against bot-detection on the broker's login page (some brokers' login pages check `navigator.webdriver`); test explicitly in headless mode, not just headed mode during development.
- Treating a successful login as proof the session will remain valid — see `token-lifecycle-live-probing` for why expiry cannot be assumed from documentation alone.
- Leaving expired token files lying around. The broker invalidated yesterday's token, but the plaintext bearer string is still on disk; a date-keyed cache that never purges accumulates credential-leak surface for zero benefit.

## Verification

- The auth routine returns a session/access token that successfully authenticates a low-cost read call (e.g., fetch account margin or profile) immediately after login.
- Re-running the auth routine from a cold start (no cached token, fresh container) succeeds end-to-end without manual intervention, for brokers where this is possible.
- No orphaned browser processes remain after the auth routine completes (`ps aux | grep chrome` should be clean).
- The routine logs which archetype path and which broker it used, so failures are traceable to a specific integration rather than a generic "auth failed."
- A redirect URL carrying the broker's *actual* parameter name (e.g. `API_Session=...` for Breeze) yields the token, and a redirect carrying none of the expected parameters raises rather than returning a truncated URL.
- The cache key advances at the broker's session boundary, not the host's midnight: verify by computing the session date for an instant just before and just after the rollover hour in the broker's timezone.
- For Indian brokers, confirm the deployment's egress IP matches the address whitelisted with the broker, and that it is stable across restarts and instance replacement.
- Deliberately feed the token exchange a wrong checksum and confirm the failure names the broker's error code, rather than surfacing as a `KeyError`.
- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/headless-broker-auth-patterns/scripts`.

## Related Skills

- `token-lifecycle-live-probing`
- `multi-broker-rate-limit-handling`
- `systemd-supervision-for-trading-bots`
- `upstox-oauth-refresh-token-rotation`
- `ibkr-tws-gateway-headless-launch`
- `centralized-secrets-management-vault-integration`
- `india-sebi-algo-trading-tagging-requirements`
