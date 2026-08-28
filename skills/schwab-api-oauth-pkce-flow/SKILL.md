---
name: schwab-api-oauth-pkce-flow
description: >-
  Use when connecting a bot to the Charles Schwab Trader API. The first finding is
  usually that Schwab does not use PKCE: its published flow is a confidential-client
  authorization-code exchange with HTTP Basic client authentication, and no Schwab
  source documents a code_challenge. Covers the flow Schwab actually documents —
  percent-encoded authorization URL, the URL-decoded callback code, Basic-auth token
  exchange, the 30-minute access token, atomic 0600 token persistence, and the hard
  7-day refresh window that no code can renew.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- schwab-trader-api
- oauth2-authorization-code
- token-lifecycle
- credential-hygiene
- rfc-7636
brokers_frameworks:
- Charles Schwab Trader API (api.schwabapi.com)
- OAuth 2.0 authorization code grant (RFC 6749)
- RFC 7636 PKCE (helper only — not part of Schwab's flow)
- schwab-py (community reference)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this when authenticating an automated trading bot or market-data client against
the Charles Schwab Trader API, including migrations off the retired TD Ameritrade
API.

**Start with the correction, because most Schwab "PKCE" tooling is built on a false
premise.** Schwab's published Trader API documentation describes a
**confidential-client authorization-code flow**. The authorization URL it documents
is `https://api.schwabapi.com/v1/oauth/authorize?client_id={key}&redirect_uri={cb}`
— no `code_challenge`, no `code_challenge_method` — and the token endpoint
authenticates the client with `Authorization: Basic base64(app_key:app_secret)`.
No Schwab source, and not the most widely used community client (`schwab-py`),
mentions PKCE anywhere. Implement the documented flow; do not send PKCE parameters
Schwab has never published support for.

**And settle the operational question before writing code.** Schwab's refresh token
is valid for 7 days from creation, refreshing does not extend that window, and
Schwab publishes no way to renew it programmatically. A Schwab-connected bot
requires a human at a browser at least once a week. That is a scheduling
constraint, not a bug to engineer around.

What this skill then covers: building the authorization URL without mangling it,
decoding the percent-encoded callback code, exchanging it with strict response
validation, persisting tokens as the credentials they are, refreshing on a buffer,
and alerting before the 7-day window closes.

## When NOT to Use

- **For a strategy that cannot tolerate a weekly manual re-authorization.** There
  is no unattended path past day 7. Choose a different broker for that strategy, or
  see `headless-broker-auth-patterns` before designing around it.
- **As a general PKCE reference.** `SchwabPKCEGenerator` is RFC 7636-correct and
  usable elsewhere, but PKCE is for public clients that cannot hold a secret.
  Schwab issues an app secret, so the client is confidential by definition.
- **As a secrets manager.** This persists tokens to a local `0600` file. For
  multi-host or multi-tenant deployments see
  `centralized-secrets-management-vault-integration` and
  `secrets-rotation-without-bot-downtime`.
- **As a risk control.** Nothing here bounds exposure, drawdown or order rate. See
  `kill-switch-and-drawdown-circuit-breakers` and `sec-rule-15c3-5-risk-controls-us`.
- **As an HTTP client.** Transport is injected, deliberately, so timeouts, TLS
  verification and retry policy stay under caller control.

## Prerequisites

- A Schwab Developer app in "Ready For Use" state, with its App Key (`client_id`)
  and App Secret.
- A registered callback URL that is **HTTPS** (loopback `https://127.0.0.1` is
  explicitly allowed), under Schwab's 255-character limit, matching byte-for-byte
  what the client sends. An HTTPS loopback listener needs a self-signed certificate.
- A human able to complete the browser login and consent, on a weekly cadence.
- A durable, owner-only path for the token file, outside version control.
- A caller-supplied `http_post_fn(url, form_payload, headers) -> dict` that raises
  on transport failure.

## Workflow

1. **Confirm the weekly re-authorization is acceptable, and schedule it.**
   - **Decision point:** if a 7-day human step breaks the operating model, stop
     here — this is a broker-selection problem, not an implementation problem.
   - Re-authorizing early is free: a fresh authorization simply starts a new 7-day
     window. Prefer a planned Sunday pre-market slot over reacting to an alert.

2. **Build the authorization URL with encoded parameters and no PKCE.**
   - **Decision point — percent-encode.** A raw `redirect_uri` truncates the query
     string at its own `?`/`&`; Schwab then compares a mangled callback against the
     registered one and rejects the login with a security error that names nothing.
   - **Decision point — omit `code_challenge`.** `get_authorization_url` sends it
     only when a caller passes one explicitly, and warns when they do, because that
     behaviour is unverified against Schwab.

3. **Capture the callback and URL-decode the code.**
   - **Decision point — check for `error` before looking for `code`.** A denied
     consent redirects with `error`; treating that as "no code yet" hangs instead of
     failing.
   - **Decision point — the code is percent-encoded and typically ends `%40`.**
     Schwab's documentation states the code "must be URL decoded prior to making the
     request". The community habit of slicing between the literals `code=` and
     `%40` truncates the trailing `@` or leaves the value encoded; the exchange then
     fails with an opaque error. `extract_code_from_callback` parses the query
     string properly.

4. **Exchange the code, and validate the response strictly.**
   - `POST /v1/oauth/token` with `Authorization: Basic base64(app_key:app_secret)`,
     `Content-Type: application/x-www-form-urlencoded`, and
     `grant_type=authorization_code&code=…&redirect_uri=…`.
   - **Decision point — never default `expires_in`.** A client that invents a
     lifetime the server did not state keeps using a dead token, and every later
     call 401s for a reason nothing in the logs explains. Absent or non-numeric is
     fatal. A missing `refresh_token` is fatal too — unattended operation is
     impossible without it.
   - **Decision point — a lost response is ambiguous, not a failure.** The
     authorization code is single-use; Schwab may already have consumed it, in which
     case retrying the same code cannot work and the recovery is a fresh browser
     authorization. `SchwabAmbiguousTokenError` marks this and leaves stored state
     untouched.
   - **Decision point — never interpolate the response into an error message.** It
     carries `access_token`, `refresh_token` and `id_token`. Echo the OAuth
     `error`/`error_description` and the key names only.

5. **Persist tokens as credentials.**
   - Temp file created at mode `0600` **before** any secret is written, `fsync`,
     then `os.replace`. A default-mode temp file is briefly world-readable; an
     unsynced write can leave a truncated file that looks like corruption.
   - **Decision point — a failed write must raise.** Logging and continuing leaves
     the operator believing the tokens survive a restart. `SchwabTokenPersistenceError`
     is raised while the in-memory state is kept, so a running process can continue
     trading and retry the write rather than discarding a token Schwab already issued.

6. **Refresh on a buffer, and never move the 7-day anchor.**
   - **Decision point — refresh at 5 minutes remaining, not on a 401.** Refreshing
     after a rejection puts a token round trip on the critical path of an order.
   - **Decision point — `refresh_expires_at` is anchored at the original
     authorization.** Re-anchoring it on each refresh silences the warning entirely
     and the bot dies without notice mid-week. `refresh_access_token` carries the
     original deadline forward unchanged.
   - **Decision point — store a rotated `refresh_token` if one is returned**, and
     keep the existing one if not. Rotation is undocumented; this is correct either
     way.
   - **Decision point — `invalid_client` means re-authorize, not retry.** That is
     how Schwab rejects an over-age refresh token, and no retry can succeed.
     `SchwabRefreshTokenExpiredError` exists so an alert binds to exactly that.

7. **Alert on the window, and gate the bearer header.**
   - Poll `is_refresh_token_expiring_soon()` hourly; at 24 hours remaining raise an
     operator alert, not a log line — the remedy needs a human.
   - `get_bearer_header()` refuses to build a header from a token inside the refresh
     buffer, so staleness fails locally instead of as a mid-order 401.

> Full step-by-step procedure: see `references/workflows.md`.
> Sourced endpoints, lifetimes and the PKCE evidence: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Implementing Schwab as a PKCE flow.** It is the premise of a lot of Schwab
  tooling and no Schwab source supports it. Worse than useless: a caller who thinks
  PKCE is protecting the exchange may under-protect what actually needs it — the app
  secret and the token file.
- **Interpolating the callback URL into the authorization URL unencoded.** It
  truncates at the first `?`/`&` and the login fails with a security error.
- **Hand-slicing the authorization code out of the callback.** The code is
  percent-encoded and usually ends `%40`; slicing on `code=`/`%40` truncates or
  under-decodes it, and the exchange fails with an error that names nothing.
- **Assuming `expires_in` when the response omits it.** The client then believes a
  dead token is live.
- **Retrying an authorization-code exchange after a timeout.** The code is
  single-use and may already be spent; the recovery is a new browser authorization.
- **Re-anchoring the 7-day refresh deadline on every refresh.** The warning never
  fires and the flow dies mid-week with no programmatic recovery.
- **Waiting for a 401 before refreshing.** The refresh then lands on the critical
  path of an order submission.
- **Treating `invalid_client` as a transient error and retrying.** It means the
  refresh window closed; only a human can fix it.
- **Discarding stored token state when a refresh fails in transport.** Schwab may
  have rotated the token; throwing away the old one guarantees a re-login that might
  not have been necessary.
- **Writing the token file with default permissions.** It holds a live access and
  refresh token — anyone who can read it can trade the account.
- **Logging or `repr`-ing token state.** One `logger.debug(state)` ships credentials
  to the log aggregator; tokens are excluded from `repr` for this reason.
- **Putting the token response in an exception message.** Three credentials go
  straight into the traceback.
- **Swallowing a token-file write failure.** The process looks healthy until it
  restarts and finds nothing.
- **Letting two processes share one token file.** The write is atomic but takes no
  cross-process lock, so concurrent refreshes are last-writer-wins — and if Schwab
  rotates the refresh token, the loser holds a stale one and forces an unplanned
  re-login. Run exactly one token owner per Schwab app and have other processes read
  the token, never refresh it.
- **Quoting an unsourced overall rate limit as a Schwab contract.** Schwab documents
  a 0–120 requests/minute *order* throttle; the commonly cited overall figure is
  community-reported.

## Verification

- **Authorization URL:** parameters are percent-encoded (a `redirect_uri`
  containing `:` and `/` never appears literally); no `code_challenge` or
  `code_challenge_method` is present by default; both appear only when a challenge
  is explicitly supplied; a padded challenge, a non-HTTPS callback, an over-length
  callback and a blank app key each raise.
- **Callback decoding:** `?code=C0.abc-def%40` yields `C0.abc-def@`; `%2B` yields
  `+`, not a space; an `error` parameter raises; zero, empty or duplicated `code`
  parameters raise.
- **Exchange request shape:** posts to `/v1/oauth/token` with
  `grant_type=authorization_code`, the decoded code, the callback,
  `Authorization: Basic base64("KEY:SECRET")` and the form content type; no
  `code_verifier` unless supplied; a colon in the app key raises before dispatch.
- **Exchange response validation:** missing `expires_in` raises and leaves state
  unset; `None`, `"soon"`, `0`, `-5`, `True`, `inf` and `NaN` all raise; a missing
  `refresh_token` raises.
- **Secret hygiene:** a rejection carrying `refresh_token`/`id_token` produces a
  message containing the OAuth error but neither credential; `repr()` of token state
  shows the expiry fields and neither token.
- **Ambiguity:** a transport exception and a non-JSON-object body each raise
  `SchwabAmbiguousTokenError` and leave prior state identical.
- **Refresh:** the 7-day deadline is byte-identical before and after a refresh (the
  regression); the request body is exactly `grant_type`/`refresh_token`; a rotated
  token is stored and reaches disk; an absent one keeps the existing value; an
  elapsed window and a missing token both raise `SchwabRefreshTokenExpiredError`
  with zero network calls; `error=invalid_client` raises the same; a transport
  failure preserves state.
- **Lifetimes:** the access buffer boundary flips at exactly `expiry - 300 s`, the
  refresh warning at exactly `expiry - 86400 s`; no state counts as expiring;
  `get_bearer_header()` raises inside the buffer and returns the Bearer header
  outside it.
- **Persistence:** state round-trips through a new manager; no `.tmp` files remain;
  the file is `0600` on POSIX; a corrupt or wrongly typed file yields `None` state
  without crashing and is left on disk for the operator; an unwritable path raises
  `SchwabTokenPersistenceError` while the in-memory token stays usable.
- **RFC 7636 helper:** the Appendix B vector
  (`dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk` →
  `E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM`) reproduces exactly; challenges are
  43 characters and unpadded; lengths 42 and 129 raise while 43 and 128 succeed.
- Run `python -m unittest discover -s skills/schwab-api-oauth-pkce-flow/scripts`
  and confirm all tests pass.

## Related Skills

- `headless-broker-auth-patterns`
- `token-lifecycle-live-probing`
- `secrets-rotation-without-bot-downtime`
- `centralized-secrets-management-vault-integration`
- `sandbox-credential-leakage-prevention`
- `broker-agnostic-adapter-interface`
- `multi-broker-rate-limit-handling`
- `structured-logging-for-post-incident-forensics`
