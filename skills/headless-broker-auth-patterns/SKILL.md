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
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a bot needs to authenticate with a broker without a human present at bot-start time — nightly restarts, systemd auto-restart, or cloud deployment where no browser session exists. Two broker archetypes exist and require different approaches; do not assume one pattern fits all brokers.

**Archetype A — REST-based headless auth (Fyers, Upstox, Alpaca, IBKR via gateway):**
The broker exposes a documented token/refresh-token endpoint. Login can be fully scripted with an HTTP client, TOTP generation, and a redirect-URL code exchange.

**Archetype B — No proper login API (ICICI Breeze):**
The broker's web login flow is the only way to get a session token. There is no documented endpoint that accepts credentials + TOTP and returns a session key programmatically. This forces browser automation (Selenium/Playwright) to drive the actual login page and intercept the redirect.

## Prerequisites

- Broker developer app registered (API key + secret, redirect URI configured)
- TOTP secret provisioned for 2FA-enabled accounts (store as encrypted secret, never plaintext in repo)
- For Archetype B: headless Chromium/Chrome available in the deployment environment (not just dev machine — this breaks silently in minimal Docker images missing browser deps)
- A secrets store (env vars, Vault, or at minimum a `.env` excluded from git) — credentials must never be hardcoded

## Workflow

### Archetype A (REST-based, e.g. Fyers)
1. Generate TOTP code from the stored secret using a standard TOTP library (`pyotp` or equivalent) — do not assume the code is valid for more than ~30s, generate it at the moment of use.
2. POST credentials + TOTP to the broker's login-step endpoint to obtain an intermediate auth code.
3. Exchange the auth code for an access token via the token endpoint, using API key + secret (some brokers require this hashed/signed per their spec — check the broker's checksum requirement, e.g. SHA-256 of `api_key + auth_code + api_secret`).
4. Persist the resulting access token to a local cache file or secrets store keyed by date, since most brokers issue tokens valid only for the trading day.
5. On subsequent bot starts same-day, attempt to reuse the cached token via a **live probe** (see `token-lifecycle-live-probing` skill) before triggering a fresh login — do not blindly re-login every restart, as some brokers rate-limit login attempts.

### Archetype B (Browser automation, e.g. Breeze)
1. Launch Selenium/Playwright in headless mode with a persistent user-data-dir if the broker sets long-lived cookies that reduce friction on subsequent logins.
2. Navigate to the broker's actual login URL (same one a human would use) — do not try to reverse-engineer a "hidden" API endpoint; ICICI Breeze does not have a session-creation API and treating scraped internal endpoints as stable is a common cause of silent breakage when the broker changes their frontend.
3. Fill username/password fields using explicit waits on element visibility, not fixed `sleep()` calls — page load time varies and fixed sleeps cause flaky failures under CI/cron load.
4. Handle 2FA: if TOTP-based, generate and submit the same way as Archetype A; if SMS/email OTP based, this cannot be fully automated — flag this to the user as requiring manual intervention or a semi-automated fallback (e.g., a webhook that receives the OTP from an email parser), and document this limitation explicitly rather than pretending full automation is possible.
5. After successful login, intercept the redirect URL (Selenium's `driver.current_url` after redirect, or a network-request listener) to extract the `session_token` / `api_session` query parameter the broker embeds in the redirect.
6. Immediately exchange this raw session token for the broker SDK's working session object (e.g., `breeze.generate_session(api_secret, session_token)`), then close/quit the browser instance — do not leave headless Chrome processes running, they accumulate as zombie processes under systemd restarts and exhaust memory over days.
7. Cache the resulting session identifier the same way as Archetype A.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Assuming a browser-driven login is a one-time setup cost — it must run on every session-token expiry, so it needs to be as reliable as the REST path, not a manual fallback script.
- Not handling headless Chrome crashes/hangs with a timeout wrapper — a hung Selenium session blocks the entire auth pipeline and, if auth runs synchronously before market open, delays the whole bot.
- Storing the TOTP secret or password in the same config file as non-sensitive settings, making it easy to accidentally commit.
- Forgetting that headless mode can behave differently from headed mode against bot-detection on the broker's login page (some brokers' login pages check `navigator.webdriver`); test explicitly in headless mode, not just headed mode during development.
- Treating a successful login as proof the session will remain valid — see `token-lifecycle-live-probing` for why expiry cannot be assumed from documentation alone.

## Verification

- The auth routine returns a session/access token that successfully authenticates a low-cost read call (e.g., fetch account margin or profile) immediately after login.
- Re-running the auth routine from a cold start (no cached token, fresh container) succeeds end-to-end without manual intervention, for brokers where this is possible.
- No orphaned browser processes remain after the auth routine completes (`ps aux | grep chrome` should be clean).
- The routine logs which archetype path and which broker it used, so failures are traceable to a specific integration rather than a generic "auth failed."

## Related Skills

- `token-lifecycle-live-probing`
- `multi-broker-rate-limit-handling`
- `systemd-supervision-for-trading-bots`
