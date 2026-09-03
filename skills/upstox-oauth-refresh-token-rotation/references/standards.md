# Broker & Framework Coverage — upstox-oauth-refresh-token-rotation

Each row records the mechanism the broker actually publishes, not the mechanism a
convenience wrapper or a generated snippet offers. Verified against the sources listed
at the bottom; broker APIs change without notice, so re-verify before relying on any row.

| Broker / API | Long-lived credential? | Access-token lifetime | Notes |
|---|---|---|---|
| **Upstox API v2/v3** | **None.** No refresh token, no `grant_type=refresh_token` | Expires at **03:30 IST the following day**, regardless of issue time; response carries no `expires_in` | Re-acquire daily via authorization-code OAuth, the v3 Access Token Request approval flow, or a read-only Analytics Token |
| Zerodha Kite Connect v3 | Not for ordinary API users; Zerodha issues refresh tokens only to exchange-approved platform partners | Expires ~06:00 IST next day | Kite's docs attribute the daily expiry to a regulatory requirement |
| Fyers API v3 | **Yes** — refresh token valid 15 days, seeded by one interactive OAuth login | Access token daily | The one genuinely sanctioned unattended path among the Indian brokers here; `appIdHash = sha256("appId:secret")` |
| Alpaca Trading API | Static `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` headers — no session, no login, no daily expiry | N/A | An optional client-credentials flow issues short-lived bearer tokens; this is not refresh-token rotation |

> **Correction to an earlier revision of this file.** It described Upstox as performing
> "single-use refresh token rotation; new refresh token issued per exchange call," and
> Alpaca as "OAuth2 refresh token rotation & long-lived API keys." Both were wrong.
> Upstox publishes no refresh credential of any kind, and Alpaca's model is static keys.
> The earlier text pointed integrations at a `https://api.upstox.com/v2/login/auth/token`
> endpoint and a `refresh_token` grant that do not exist.

## Upstox token acquisition paths

| Path | Endpoint | Body encoding | Order-capable | Notes |
|---|---|---|---|---|
| Authorization code | `POST https://api.upstox.com/v2/login/authorization/token` (`grant_type=authorization_code`), preceded by the dialog at `/v2/login/authorization/dialog` | `application/x-www-form-urlencoded` | Yes | `code` is single-use. Response also carries an `extended_token` (read-only) — do not bind it as the trading credential |
| Access Token Request | `POST https://api.upstox.com/v3/login/auth/token/request/{client_id}` with `{"client_secret": ...}` | `application/json` | Yes | Returns `authorization_expiry` + `notifier_url`; the token itself arrives at the registered webhook on user approval (in-app + WhatsApp prompt). Individual accounts only |
| Analytics Token | Generated in the Developer Apps console; no OAuth redirect | N/A | **No** | 1-year validity, one active per account (regenerating revokes the previous). GET only. Account-scoped APIs additionally require Static IP |

The notifier webhook delivers:

```json
{"client_id": "...", "user_id": "...", "access_token": "...", "token_type": "Bearer",
 "expires_at": "1731448800000", "issued_at": "1731412800000", "message_type": "access_token"}
```

`expires_at` and `issued_at` are **epoch milliseconds, as strings**. As an independent
check on the 03:30 rule: `1731448800000` ms is 2024-11-13 03:30:00 IST.

## Authentication error codes

| Code | HTTP | Meaning | Correct response |
|---|---|---|---|
| `UDAPI100050` | 401 | Invalid token used to access API | Re-authenticate |
| `UDAPI100016` | 401 | Invalid credentials | Configuration fault — do not retry |
| `UDAPI100067` | 403 | API not permitted with an `extended_token` | Use an order-capable token; retrying cannot help |
| `UDAPI100073` | 403 | `client_id` is inactive | Configuration fault — do not retry |

Error envelope: `{"status": "error", "errors": [{"error_code": ..., "message": ...,
"property_path": null, "invalid_value": null}]}`. The camelCase `errorCode` spelling is
deprecated in favour of `error_code` but still appears in the wild.

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

**Jurisdiction: India (SEBI / NSE).** These bind *brokers*, but they constrain what a
client integration can be built on — and they explain why no Upstox refresh credential
exists to rotate.

NSE circular **NSE/INVG/67858** dated 05-May-2025 ("Safer participation of retail
investors in Algorithmic trading"), Annexure — Implementation Standards issued under
clause 7 of SEBI circular **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013** dated
04-Feb-2025:

| Para | Requirement (verbatim where quoted) | Effect here |
|---|---|---|
| A.8 | "All API sessions shall be compulsorily logged out every day before the start of the next trading day" | No multi-day session persistence. Daily re-acquisition is mandatory, which is what a long-lived refresh token would circumvent |
| A.1, I.e | Clients "must mandatorily provide the stockbroker with a static IP address(es)"; access only via "a unique vendor client specific API key and static IP whitelisted by the broker" | Ephemeral cloud egress IPs cannot authenticate |
| A.6 | Mapped static IPs may be updated "not more than once a calendar week" | You cannot chase a rotating IP by re-registering |
| I.c | Brokers "shall be required to have OAuth (Open Authentication) based authentication only or any authentication mechanism allowed / communicated by the Exchange / SEBI from time to time" | The browser dialog and the approval flow are the sanctioned surfaces |
| I.d | "System shall authenticate client access to IBT / STWT / other API through two factor authentication" | 2FA is not optional on the API surface |

Applicability was extended and phased: fully applicable to all stock brokers from
**01-Apr-2026** per SEBI circular **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132** dated
30-Sep-2025. Para J.1 excludes Direct Market Access (DMA), governed by its own provisions.

**Advisory, not regulatory:** IETF **RFC 9700**, *Best Current Practice for OAuth 2.0
Security* (January 2025), is the current OAuth security BCP; it obsoletes the guidance
role previously served by RFC 6819, and RFC 6749 is the base authorization framework
rather than a security BCP. Its §4.14 refresh-token guidance (sender-constraining or
rotation for public clients) is what an Upstox integration would follow *if* Upstox
issued refresh tokens — it does not, so the applicable parts here are its general
guidance on treating bearer tokens as sensitive credentials at rest and in transit.
An earlier revision of this file cited "OAuth 2.0 Security Best Current Practice
(RFC 6749 / RFC 6819)", conflating three different documents.

*Nothing here is legal or compliance advice. Confirm current requirements and your
broker's terms of use before deploying.*

## Sources

- Upstox Developer API, "Get Token" — https://upstox.com/developer/api-documentation/get-token/
- Upstox Developer API, "Authentication" — https://upstox.com/developer/api-documentation/authentication/
- Upstox Developer API, "Access Token Request" — https://upstox.com/developer/api-documentation/access-token-request/
- Upstox Developer API, "Analytics Token" — https://upstox.com/developer/api-documentation/analytics-token/
- Upstox Developer API, "Error codes" and "Response structure" — https://upstox.com/developer/api-documentation/error-codes/
- Upstox Community, "refresh_token — OAuth 2.0 authorization code flow" (Upstox staff, 01-Aug-2025: "We do not support refresh tokens. Our access token is valid until 3:30 AM and expires after that.") — https://community.upstox.com/t/refresh-token-oauth-2-0-authorization-code-flow/10363
- NSE circular NSE/INVG/67858, 05-May-2025 — https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf
- Kite Connect v3 documentation, "User / login flow" — https://kite.trade/docs/connect/v3/user/
- Fyers support KB, "What is the function of the refresh token in FYERS?" — https://support.fyers.in/portal/en/kb/articles/what-is-the-function-of-the-refresh-token-in-fyers
- Alpaca, "Authentication" — https://docs.alpaca.markets/docs/authentication
- IETF RFC 9700, *Best Current Practice for OAuth 2.0 Security* — https://www.rfc-editor.org/info/rfc9700/
