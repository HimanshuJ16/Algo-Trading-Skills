# Broker & Framework Coverage — token-lifecycle-live-probing

Each row records what the broker's own documentation states, and marks separately
anything that is only community-reported. Broker APIs change without notice —
re-verify before relying on any row. Sources are listed at the bottom.

| Broker / Framework | Read-only probe endpoint | Documented token lifetime | Auth-failure signal |
|---|---|---|---|
| Zerodha Kite Connect | `GET /user/profile`, `GET /user/margins` | Expires at **6 AM the next day**, attributed by Kite to a regulatory requirement. Also dies early if "invalidated using the API, or invalidated by a master-logout from the Kite Web trading terminal" | HTTP `403`, `error_type: TokenException` — "Preceded by a 403 header, this indicates the expiry or invalidation of an authenticated session" |
| Fyers API v3 | `GET /profile` | **No time-of-day is published.** Fyers documents the *refresh* token as "valid for 15 days"; the access token is a daily token with no stated expiry instant | HTTP `401` with body `{"s":"error","code":-16,"message":"Could not authenticate the user"}` *(community-reported; Fyers publishes no error-code table publicly reachable at time of writing)* |
| ICICI Breeze API | `GET /breezeapi/api/v1/customerdetails`, `GET /breezeapi/api/v1/funds` | "valid for 24hrs or before midnight, whichever is earlier" — so the same token has a different deadline depending on issue time | Documented HTTP codes include `401`/`403`, **but** every response is wrapped as `{"Success":…, "Status":<http-style code>, "Error":…}`, so an error can arrive under HTTP 200 |

## Why this skill probes instead of trusting the timestamp

The Fyers row is the argument in miniature. A TTL constant for Fyers cannot be
sourced, because Fyers does not publish one. Community answers put the expiry near
6 AM IST, but that is one forum reply from a non-staff member — not a figure to gate
live trading on. An earlier revision of this file asserted "around 2:00–3:00 AM IST";
no Fyers source supports that and it has been removed.

The regulatory driver behind the daily death of Indian broker tokens *is* sourced:
NSE circular NSE/INVG/67858 (05-May-2025), Annexure para A.8 — "All API sessions shall
be compulsorily logged out every day before the start of the next trading day." Kite's
documentation attributes its own 6 AM expiry to a regulatory requirement. So the
daily invalidation is real and mandatory; what is *not* reliably documented is the
instant it happens per broker. That gap is exactly what a probe closes and a
timestamp cannot.

## Status-code classification

Classify from the broker's own documentation, not from a general HTTP intuition.
The defaults in `scripts/token_probe.py` are drawn from the rows above:

| Bucket | Codes | Why |
|---|---|---|
| INVALID (re-authenticate) | `401`, `403` | The only codes the brokers above document as session/token failures |
| AMBIGUOUS (retry, then escalate) | `408`, `425`, `429`, all `5xx`, timeouts, no response | Kite documents `429` as rate limiting and `500`/`502`/`503`/`504` as server-side; Breeze documents `408`. None of them speak to the token |
| AMBIGUOUS (unrecognised) | everything else, incl. `400`, `404`, `405`, `410`, `3xx` | Kite documents `400` as bad parameters, `404` as resource not found, `405` as wrong method, `410` as gone. These are client/config defects; re-authentication cannot fix any of them, and guessing "invalid" spends a login on your own bug |

Kite's documented rate limit context matters here: `429` is a *documented*
response, not an anomaly. Fyers publishes limits of 10 requests/second and 200/minute
*(community/blog-sourced)* and returns `{"code":429,"message":"request limit
reached","s":"error"}` on breach. A probe that reads `429` as revocation
re-authenticates precisely when the broker is already throttling.

## Envelope-wrapped errors

Two of the three brokers can report an error inside a successful HTTP response:

- **ICICI Breeze** — every response is `{"Success":…, "Status":…, "Error":…}` where
  `Status` carries an HTTP-style code. A status-code-only classifier reads a dead
  session as VALID.
- **Fyers API v3** — error bodies carry `"s": "error"` with a numeric `code`. In the
  observed 401 case the HTTP status agrees with the body, but the body is the
  authoritative signal.

`classify_probe_response` accepts a `body_classifier` for this. It is consulted only
for 2xx responses, so it can downgrade an apparent success but can never upgrade a
transport-level `401` into "keep trading".

## Probe endpoint side-effect check

`GET /breezeapi/api/v1/funds` reads funds; `POST` to the *same path* sets funds
allocation. Confirm the method, not just the path, before designating a probe.
Breeze's own SDK validates a session by calling `customerdetails` over `GET`, which
makes it a well-precedented probe target.

## Regulatory & Operational Notes

**Jurisdiction: India (SEBI / NSE)** for all three brokers listed. The daily-logout
mandate above (NSE/INVG/67858 Annexure A.8, issued under SEBI circular
SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 dated 04-Feb-2025) is what makes
"probe at every bot start" the right default rather than a defensive habit — there is
no compliant design in which a session survives to the next trading day.

Do not generalise this to other jurisdictions. Token lifetimes elsewhere are set by
the broker, not by an exchange circular; a US or EU broker's session may legitimately
span days. The *probing discipline* transfers; the daily-expiry assumption does not.

`references/` in `headless-broker-auth-patterns` carries the fuller regulatory table,
including the static-IP and OAuth-only requirements that constrain what the
re-authentication path may be built on.

*Nothing here is legal or compliance advice. Confirm current requirements and your
broker's terms of use before deploying.*

## Sources

- Kite Connect v3, "Exceptions and error handling" (status/error_type table) — https://kite.trade/docs/connect/v3/exceptions/
- Kite Connect v3, "User / login flow" (6 AM expiry, master-logout, `/user/profile`, `/user/margins`) — https://kite.trade/docs/connect/v3/user/
- Fyers support KB, "What is the function of the refresh token in FYERS?" (15-day refresh token; no access-token time-of-day stated) — https://support.fyers.in/portal/en/kb/articles/what-is-the-function-of-the-refresh-token-in-fyers
- Fyers community, "Unable to Authenticate User – Fyers API Token Issue (401 Error)" (401 + `code:-16` body) — *community-reported* — https://fyers.in/community/api-algo-trading-bihtdkgq/post/unable-to-authenticate-user---fyers-api-token-issue-401-error-EqlisqB8vmH7wJS
- Fyers community, "{'code': 429, 'message': 'request limit reached', 's': 'error'}" — *community-reported* — https://fyers.in/community/api-algo-trading-bihtdkgq/post/code-429-message-request-limit-reached-s-error-4lmmfzFrl8gWmSe
- ICICI Direct, "What is a Session Key & How to Generate It for Using Breeze API" (24h or midnight, whichever is earlier) — https://www.icicidirect.com/futures-and-options/api/breeze/article/what-is-a-session-key-and-how-to-generate-it-for-using-breezeapi
- Breeze API Reference (response envelope, status codes incl. 408, `customerdetails` and `funds` endpoints) — https://api.icicidirect.com/breezeapi/documents/index.html
- Breeze Python SDK (`generate_session` validates via a `GET` to `customerdetails`) — https://github.com/Idirect-Tech/Breeze-Python-SDK
- NSE circular NSE/INVG/67858, 05-May-2025, Annexure para A.8 — https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf
