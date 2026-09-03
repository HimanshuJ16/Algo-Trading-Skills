---
name: upstox-oauth-refresh-token-rotation
description: >-
  Use when holding an Upstox API v2 or v3 access token across time. Upstox issues no
  refresh token and the access token dies at 03:30 IST daily, so this covers expiry
  derivation, single-flight daily re-authentication and atomic persistence.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, upstox-api-v2, oauth2, access-token-lifecycle, daily-session-expiry
  brokers_frameworks: Upstox API v2/v3
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this whenever a bot holds an Upstox access token across time — order placement, market-data streaming, or any process that outlives a single request.

**Read this first, because the skill's own slug is misleading.** Upstox does not issue refresh tokens and publishes no `grant_type=refresh_token` exchange. Upstox staff state it plainly: *"We do not support refresh tokens. Our access token is valid until 3:30 AM and expires after that."* The Get Token API documents `authorization_code` as the only grant, and its response contains no `refresh_token` field. This skill retains the historical slug for cross-reference stability; what it actually covers is the **daily access-token lifecycle**.

The two facts that drive every design decision here:

| Fact | Consequence |
|---|---|
| The token expires at a **fixed wall-clock instant** — 03:30 IST the following day — regardless of when it was issued, and the response carries no `expires_in` | Expiry must be *derived* from the 03:30 IST rule. A `now + 86400` model overstates validity by up to ~24h |
| There is no refresh credential; every re-acquisition path costs a human interaction or a console action | Re-auth must be single-flighted and the result persisted atomically, or you wake the user N times |

The three documented ways to obtain a token:

| Path | Endpoint / mechanism | Unattended? | Can place orders? |
|---|---|---|---|
| **Authorization code** | Browser dialog → `POST https://api.upstox.com/v2/login/authorization/token`, `grant_type=authorization_code` | No — a human completes the dialog daily | Yes |
| **Access Token Request** | `POST https://api.upstox.com/v3/login/auth/token/request/{client_id}` → user approves an in-app/WhatsApp prompt → token delivered to your registered `notifier_url` | Near-unattended: no browser, but the user taps approve. Individual accounts only | Yes |
| **Analytics Token** | Generated from the Developer Apps console; 1-year validity, one active per account | Yes | **No** — GET only; order APIs return 403 `UDAPI100067` |

## When NOT to Use

- **Do not use this to build a multi-day Upstox session.** For an Indian broker that is not merely unsupported, it is precluded: NSE circular NSE/INVG/67858 (05-May-2025), Annexure para A.8, requires that *"All API sessions shall be compulsorily logged out every day before the start of the next trading day."* Daily re-acquisition is the design, not a workaround.
- **Do not reach here for a generic OAuth refresh-rotation pattern.** Nothing on this page transfers to a broker that genuinely rotates refresh tokens. For that shape — Fyers' 15-day refresh token — see `headless-broker-auth-patterns`.
- **Do not use the Analytics Token as a trading credential.** It cannot place, modify, or cancel orders at any price.

## Prerequisites

- Upstox developer app in approved status, with `API_KEY` (`client_id`), `API_SECRET` (`client_secret`), and a registered `redirect_uri`.
- For the Access Token Request path: a registered `notifier_url` webhook and an individual (not corporate) account.
- **A static IP registered with the broker.** NSE/INVG/67858 A.1 and I.e make static-IP whitelisting mandatory for API access, and A.6 permits changing the mapped IP not more than once a calendar week. Account-scoped Analytics Token calls additionally require Static IP to be enabled. An ephemeral cloud egress IP will not authenticate.
- A token store the bot can write atomically, with permissions that keep a plaintext bearer token off other accounts on the host.
- A `threading.Lock`/`asyncio.Lock` if more than one worker reads the token.

## Workflow

1. **Load persisted token state.** Read `access_token`, `expires_at`, `source`, and `read_only`. Treat an unreadable or schema-invalid file as *no token* — never as a usable one. Refuse to load a record with no `expires_at` rather than substituting a default; the default is how the 24-hour-validity bug gets back in.

2. **Decide expiry against the real boundary, not a duration.** Compute the next 03:30 IST instant strictly after issuance. Upstox's own examples: issued 20:00 IST Tuesday → expires 03:30 IST Wednesday (~7.5h); issued 02:30 IST Wednesday → expires 03:30 IST **that same** Wednesday (~1h). Where the broker supplies an authoritative `expires_at` (the notifier webhook does), prefer it over the derived value — but parse it as **epoch milliseconds, delivered as a string**.

3. **Apply a buffer before starting work, not to "refresh early".** Treat the token as expiring if it dies within ~900s. There is nothing to refresh; the buffer exists so a strategy never *begins* a cycle on a token that will die between an order submission and its status poll.

4. **Single-flight the re-authentication.** Acquire the lock, then **re-check expiry inside it**. Without the recheck the lock serialises the prompts instead of collapsing them, and ten workers waking at 03:30 IST still send ten approval pushes to the user's phone. Exactly one re-auth call must escape.

5. **Choose the re-acquisition path deliberately.** The authorization-code exchange body is **form-urlencoded** (`Content-Type: application/x-www-form-urlencoded`); the v3 Access Token Request body is **JSON**. Do not generalise one to the other. The authorization `code` is single-use — a failed exchange needs a fresh code, never a replay.

6. **Validate what re-auth returned before trusting it.** Reject an empty token, and reject one whose `expires_at` is already in the past. An already-dead token is nearly always a seconds/milliseconds mix-up, and accepting it produces a hot re-auth loop that spams the user with approval prompts.

7. **Persist atomically, then publish, then release.** Write to a temp file opened `0600` and `os.replace` it into position, all before assigning to in-memory state and before releasing the lock. **Let persistence failures propagate.** A token that exists only in RAM works until the next restart, at which point an unattended bot simply cannot start.

8. **Gate write operations on the token's capability.** At order-placement call sites, require a non-read-only token and fail locally. Discovering that your credential is read-only from a rejected order — 403 `UDAPI100067` — is the expensive way to learn it.

9. **Classify failures by Upstox's own error code, not by message text.** `UDAPI100050` (invalid/expired token) means re-authenticate. `UDAPI100016` (invalid credentials) and `UDAPI100073` (inactive `client_id`) are configuration faults that re-authentication will never fix — retrying them just burns login attempts.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Building a refresh-token exchange against Upstox at all.** There is no `grant_type=refresh_token` and no `https://api.upstox.com/v2/login/auth/token` endpoint. Any code, library, or generated snippet offering Upstox "refresh token rotation" is targeting an API surface that does not exist. (The similarly-spelled real endpoint, `/v3/login/auth/token/request/{client_id}`, is the user-approval flow — a different mechanism entirely.)
- **Dating the token as `now + expires_in`, or `now + 86400`.** The response has no `expires_in`, and validity is a fixed instant, not a duration. A token minted at 20:00 IST under this model is believed valid until 20:00 the next day — roughly 16.5 hours past its real death, spanning the entire next trading session. The bot reports itself authenticated while every call returns 401 `UDAPI100050` from market open onward.
- **Reading the epoch-millisecond `expires_at` from the notifier webhook as seconds.** `"1731448800000"` interpreted as seconds dates the token to the year 56000 and silently disables every expiry check downstream. It is also a *string*, so a naive numeric comparison against `time.time()` raises rather than compares.
- **Treating HTTP 200 as success.** Upstox returns errors in the body as `{"status": "error", "errors": [{"error_code": ..., "message": ...}]}`. Decode and inspect the envelope; note that the `errorCode` camelCase spelling is deprecated in favour of `error_code`, and both still appear in the wild.
- **Checking expiry outside the lock and re-authenticating inside it, without re-checking.** Every thread that queued on the lock proceeds to re-authenticate in turn. For the approval flow this is N phone prompts and likely a rate limit; for the OAuth flow it is N humans that do not exist.
- **Swallowing persistence errors.** Logging "failed to persist" and returning the token anyway leaves the process working and the next restart unauthenticated — a failure that surfaces hours later, typically at a 03:30-adjacent systemd restart with nobody watching.
- **Writing the token file at default umask.** It is a plaintext bearer credential; open it `0600` from the start rather than `chmod`-ing after, which leaves a window where it is world-readable.
- **Keying the token cache on the host's local calendar date.** The session boundary is 03:30 IST. A UTC-hosted bot rolls its key at the wrong instant in both directions — reusing a flushed token, or forcing a needless re-auth that wakes a human.
- **Using an `extended_token` or Analytics Token for order flow.** Both are read-only; order APIs reject them with 403 `UDAPI100067`. The `extended_token` arrives in the same Get Token response as the real access token, which makes it easy to bind the wrong one.
- **Retrying `UDAPI100016` or `UDAPI100073` as if they were expiry.** Bad credentials and an inactive `client_id` are not transient; a retry loop against them accomplishes nothing and can trip login throttles.

## Verification

- Feed the expiry derivation Upstox's two documented instants and confirm the boundary lands at 03:30 IST — including that a 02:30 IST issuance expires the **same** morning, not the next one.
- Feed it an instant expressed in UTC and confirm the boundary is computed after conversion to IST, not from the raw wall-clock fields.
- Confirm the derived expiry for a 20:00 IST issuance is strictly earlier than `issued + 86400`, and that the next session's 09:15 IST market open falls after the real expiry — this is the regression the fixed-duration model failed.
- Parse the documented notifier payload and confirm `"1731448800000"` resolves to 2024-11-13 03:30:00 IST; confirm a seconds-valued timestamp is rejected rather than accepted.
- Launch concurrent workers against an expired token and assert the re-authentication callback ran **exactly once** and every worker received the same token.
- Request a token with write capability while holding a read-only Analytics token and confirm it raises locally, carrying error code `UDAPI100067`, before any order is sent.
- Point the manager at an unwritable path and confirm the persistence failure propagates and in-memory state is **not** updated.
- Confirm the persisted file is mode `0600`, contains no `refresh_token` field, and leaves no `.tmp` file behind.
- Restart the process and confirm the persisted token is reloaded and considered valid without re-authenticating.
- Run `python -m unittest discover -s skills/upstox-oauth-refresh-token-rotation/scripts` and confirm all tests pass.

## Related Skills

- `headless-broker-auth-patterns`
- `token-lifecycle-live-probing`
- `multi-broker-rate-limit-handling`
- `secrets-rotation-without-bot-downtime`
- `india-sebi-algo-trading-tagging-requirements`
- `webhook-based-order-fill-notifications`
