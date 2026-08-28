# Broker Integration Standards — schwab-api-oauth-pkce-flow

Every row below is traced to the source in the **Source** column. Rows marked
**Inferred** are this skill's conservative judgement, not published Schwab
behaviour. Where community sources disagree with each other, the disagreement is
stated rather than resolved by guesswork.

## The PKCE question — read this first

| Claim | Finding | Source |
|---|---|---|
| "Schwab requires OAuth 2.0 PKCE (RFC 7636)" | **Not supported by any Schwab source.** Schwab's Trader API documentation describes a confidential-client authorization-code flow. The authorization URL template it publishes is `https://api.schwabapi.com/v1/oauth/authorize?client_id={CONSUMER_KEY}&redirect_uri={APP_CALLBACK_URL}` — no `code_challenge`, no `code_challenge_method`. | Schwab Trader API documentation (archived full text) |
| Client authentication at the token endpoint | HTTP Basic: `Authorization: Basic {BASE64(client_id:client_secret)}`. A client that holds a secret is a *confidential* client, which is precisely the case PKCE was not designed for. | Schwab Trader API documentation; corroborated by every community client below |
| Does any Schwab source mention PKCE? | No. Neither the Schwab documentation nor `schwab-py` (the most widely used Python client) references `code_challenge`, `code_verifier`, or PKCE anywhere. | Schwab Trader API documentation; schwab-py authentication docs |

**Consequence for this skill.** PKCE is a genuine, correct standard — it is simply
not part of Schwab's published flow. The reference client therefore implements the
documented confidential-client flow and sends **no** PKCE parameters unless a
caller explicitly supplies a `code_challenge`. `SchwabPKCEGenerator` remains a
correct RFC 7636 implementation for callers who need one; it is not wired into the
Schwab path.

**Why this matters beyond pedantry.** Adding an undocumented parameter to an
authorization request is unverified behaviour: some authorization servers ignore
unknown parameters, others reject the request. More importantly, a caller who
believes PKCE is protecting the exchange may under-protect the thing that actually
does — the app secret in the Basic header, and the token file on disk.

## Endpoints and flow

| Item | Value | Source |
|---|---|---|
| Authorization endpoint | `https://api.schwabapi.com/v1/oauth/authorize` | Schwab Trader API documentation |
| Authorization parameters | `client_id`, `redirect_uri` (this skill also sends `response_type=code` for RFC 6749 conformance) | Schwab Trader API documentation |
| `scope` on the authorization request | Not present in Schwab's published template | Schwab Trader API documentation |
| Token endpoint | `https://api.schwabapi.com/v1/oauth/token` | Schwab Trader API documentation |
| Token request content type | `application/x-www-form-urlencoded` | Schwab Trader API documentation |
| Grant types | `authorization_code`, `refresh_token` | Schwab Trader API documentation |
| `authorization_code` body | `grant_type`, `code`, `redirect_uri` | Schwab Trader API documentation |
| `refresh_token` body | `grant_type=refresh_token`, `refresh_token={value}` | Schwab Trader API documentation |
| Token response fields | `expires_in`, `token_type` (`Bearer`), `scope` (`api`), `access_token`, `refresh_token`, `id_token` (JWT) | Schwab Trader API documentation |
| Authenticated API calls | `Authorization: Bearer {access_token}` | Schwab Trader API documentation |

## The authorization code is percent-encoded

| Item | Finding | Source |
|---|---|---|
| Redirect behaviour | The browser lands on a 404 page; the `code` is in the address bar's query string | Schwab Trader API documentation |
| Decoding requirement | "The 'code' within this request must be URL decoded prior to making the request" — the documented example shows `%40` becoming `@` | Schwab Trader API documentation |
| Common failure | Community examples extract the code by slicing between the literals `code=` and `%40`, which truncates the trailing `@` or leaves the value encoded. The exchange then fails with an unhelpful error. Use a real query-string parser. | Community guides (Carsten Savage; wasyaco mirror) |

## Token lifetimes

| Item | Value | Source |
|---|---|---|
| Access token | 30 minutes. "Requests associated with an access token older than thirty minutes are rejected." | Schwab Trader API documentation; schwab-py authentication docs |
| Refresh token | "A Trader API refresh token is valid for 7 days after creation." | Schwab Trader API documentation |
| After refresh expiry | "If the refresh token is no longer valid, App Authorization (Step 1) and Access Token Creation (Step 2) must be repeated to restart the OAuth Flow." | Schwab Trader API documentation |
| Rejection symptom | "requests for a new access token using a refresh token older than seven days are rejected with an `invalid_client` error" | schwab-py authentication docs |
| Does refreshing extend the 7-day window? | **No.** The window runs from creation and is not reset by use. Schwab publishes no mechanism to renew a refresh token programmatically. | schwab-py authentication docs; community guides |
| Access-token refresh buffer (300 s) | **Inferred** — this skill's local default, not a Schwab figure | This skill |
| Refresh-expiry warning (24 h) | **Inferred** — this skill's local default, chosen so an alert lands at least one business day before a human must re-authorize | This skill |

### A conflict worth knowing about

Schwab's refresh-grant response includes a `refresh_token` field annotated "Valid
for 7 days", which some community write-ups read as the window restarting on every
refresh. The weight of evidence is against that reading: schwab-py documents a hard
`invalid_client` rejection at seven days regardless of refresh activity, and Schwab
states the token is valid "7 days after creation". This skill therefore takes the
conservative position — **the deadline is anchored at the original authorization and
never moved forward** — because the failure modes are asymmetric. Warning early
costs an unnecessary re-login; warning late means the bot dies mid-week with no
programmatic recovery.

The client still *stores* whatever `refresh_token` value comes back, so it stays
correct whether or not the value rotates.

## Callback URL constraints

| Item | Finding | Source |
|---|---|---|
| Scheme | "Callback URLs must be HTTPS." | Schwab Trader API documentation |
| Loopback | "Local host Callback URL can be: `https://127.0.0.1`" | Schwab Trader API documentation |
| Length | "There is a 255 character limit on this field including all URLs listed." | Schwab Trader API documentation |
| Exact match | schwab-py requires the value to "exactly match the value you've entered in your application configuration, otherwise login will fail with a security error"; it also restricts the host to `127.0.0.1`. Schwab's own documentation does not state the exact-match rule explicitly. | schwab-py authentication docs |
| Practical implication | An HTTPS loopback callback needs a self-signed certificate on the local listener | schwab-py authentication docs |

## Rate limits

| Item | Finding | Source |
|---|---|---|
| Order throttle | "Throttle limits for orders can be set from zero (0) to 120 requests per minute per account." Applies to PUT/POST/DELETE order requests; GET requests are described as unthrottled. | Schwab Trader API documentation |
| Overall API limit | Community sources commonly cite ~120 calls/minute overall and HTTP 429 on breach. This figure is **community-reported**, not quoted from Schwab's own documentation — treat it as a planning assumption, not a contract. | QuantConnect brokerage docs; community clients |
| Token-endpoint limit | **None published.** Do not build a retry loop that hammers `/oauth/token`. | Absence of any Schwab publication |

## Credential-handling requirements (this skill's position, not a Schwab rule)

| Item | Requirement | Rationale |
|---|---|---|
| Token file permissions | `0600`, owner only | The file holds a live `access_token` and `refresh_token`. Anyone who can read it can trade the account until the refresh window closes. |
| Token file writes | Temp file created with `0600` *before* any secret is written, `fsync`, then `os.replace` | A non-atomic write can leave a truncated file that is indistinguishable from a corrupt one; a default-mode temp file is briefly world-readable. |
| Exception messages | Never interpolate a token response into an error string | Schwab's response carries three credentials (`access_token`, `refresh_token`, `id_token`). Log the OAuth `error`/`error_description` and the key names only. |
| Concurrent writers | One token owner per Schwab app | The atomic write takes no cross-process lock. Concurrent refreshes are last-writer-wins, and a rotated refresh token can be lost by the losing process. |
| `repr` of token state | Tokens excluded | A single `logger.debug(state)` otherwise ships credentials to the log aggregator. See `structured-logging-for-post-incident-forensics`. |

## Sources

- Schwab Trader API documentation, archived full text — https://archive.org/stream/schwabtraderapi/schwabapi_djvu.txt
  (Schwab's own `developer.schwab.com` portal serves HTTP 403 to unauthenticated
  clients, so the archived copy of the official PDF is cited here. Re-verify
  against the live portal when you have developer-account access.)
- schwab-py — "Authentication and Client Creation" — https://schwab-py.readthedocs.io/en/latest/auth.html
- Charles Schwab brokerage integration — QuantConnect documentation — https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/brokerages/charles-schwab
- "The (Unofficial) Guide to Charles Schwab's Trader APIs", Carsten Savage — https://medium.com/@carstensavage/the-unofficial-guide-to-charles-schwabs-trader-apis-14c1f5bc1d57 (mirror: https://wasyaco.com/node/470)
- RFC 7636, *Proof Key for Code Exchange by OAuth Public Clients* — https://www.rfc-editor.org/rfc/rfc7636 (s4.1 verifier length/alphabet; s4.2 unpadded base64url; Appendix B test vector)
- RFC 6749, *The OAuth 2.0 Authorization Framework*, s5.2 (error response fields) — https://www.rfc-editor.org/rfc/rfc6749
