# Broker & Framework Coverage — headless-broker-auth-patterns

Each row records the mechanism the broker actually publishes, not the mechanism a
reverse-engineered library offers. Verified against the sources listed at the bottom;
broker APIs change without notice, so re-verify before relying on any row.

| Broker / Framework | Archetype & Authentication Standard | Unattended ceiling |
|---|---|---|
| Fyers API v3 | **C** — one interactive OAuth login seeds a refresh token; `validate-refresh-token` exchanges it for daily access tokens. `appIdHash = sha256("appId:secret")` | Refresh token valid 15 days, then a human must redo the OAuth login |
| Alpaca Trading API | **D** — static `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` headers on every request; no session, no login flow, no daily expiry (an optional client-credentials flow issues 15-minute bearer tokens instead) | Indefinite; the risk is key custody, not session acquisition |
| IBKR TWS/Gateway API | **E** — a supervised long-running TWS/IB Gateway process holds the session (IBC, IBAutomater); 2FA is approved out-of-band via IBKR Mobile push | Not fully unattended: IBKR forces periodic restart, and card/device 2FA cannot be automated at all |
| Upstox API v2/v3 | **B/other** — browser-based OAuth dialog only. Upstox states "There is no public endpoint for other applications to directly log the customer into their upstox.com." Documented alternatives are a manual-approval token request and a read-only Analytics Token | No documented fully-unattended order-placing path |
| Zerodha Kite Connect | **A (unofficial) / OAuth** — documented flow is the browser login endpoint returning `request_token`; `checksum = sha256(api_key + request_token + api_secret)`, no separators. Zerodha states automating the login violates the API terms of use | Access token expires ~06:00 IST next day; no refresh token |
| ICICI Breeze API | **B** — no session-creation API exists. Manual browser login at `api.icicidirect.com/apiuser/login?api_key=...`; the redirect carries `API_Session` (that capitalisation), exchanged via `generate_session(api_secret, session_token)` | Session key valid 24h or until midnight, whichever is earlier |

> The earlier revision of this table classified Upstox, Alpaca and IBKR all as
> "Archetype A (REST)". That was wrong in a way that matters: it pointed integrations at
> a scripted-login endpoint for brokers that publish no such endpoint, and it obscured
> the fact that Fyers' refresh token — the one genuinely sanctioned unattended path
> among the Indian brokers here — existed at all.

## Checksum Formula Reference

- **Fyers:** `sha256(app_id + ":" + secret_key)` — the `auth_code` is sent as a separate
  `code` field in the token-exchange payload, never included in the hash. Including it
  produces error `-371` ("Please provide sha256 hash of appId and app secret").
  The same `appIdHash` is required by `validate-refresh-token`.
- **Zerodha:** `sha256(api_key + request_token + api_secret)` — concatenated with no
  separators, per the Kite Connect v3 documentation.

## Regulatory & Operational Notes

**Jurisdiction: India (SEBI / NSE).** Four of the six brokers above are Indian, so this
applies to most of this skill's surface. It binds *brokers*, but constrains what a client
integration can be built on.

NSE circular **NSE/INVG/67858** dated 05-May-2025 ("Safer participation of retail
investors in Algorithmic trading"), Annexure — Implementation Standards issued under
clause 7 of SEBI circular **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013** dated
04-Feb-2025:

| Para | Requirement (verbatim where quoted) | Effect on a headless integration |
|---|---|---|
| A.1, I.e | Clients "must mandatorily provide the stockbroker with a static IP address(es)"; access only via "a unique vendor client specific API key and static IP whitelisted by the broker"; open APIs not permitted | Ephemeral cloud egress IPs cannot authenticate. Provision a static/elastic IP first |
| A.6 | Mapped static IPs may be updated "not more than once a calendar week" | You cannot chase a rotating IP by re-registering |
| A.8 | "All API sessions shall be compulsorily logged out every day before the start of the next trading day" | No multi-day session persistence. Daily re-acquisition is mandatory, which is what makes the session-date cache key correct |
| I.c | Brokers "shall be required to have OAuth (Open Authentication) based authentication only or any authentication mechanism allowed / communicated by the Exchange / SEBI from time to time" | Scripted credential posts to undocumented internal endpoints are not the sanctioned mechanism |
| I.d | "System shall authenticate client access to IBT / STWT / other API through two factor authentication" | 2FA is not optional on the API surface |
| I.h | Retail algorithms "should be hosted on [brokers'/empanelled providers'] servers" | Affects where the bot may run, not only how it authenticates |

Applicability was extended and phased: fully applicable to all stock brokers from
**01-Apr-2026** per SEBI circular **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132** dated
30-Sep-2025. Note para J.1 — these standards do not apply to Direct Market Access (DMA),
which remains governed by its own provisions.

Broker-level position, distinct from the circular: Zerodha staff state that automating
the Kite Connect login "was never allowed to begin with… you were in violation of the
terms of use of the APIs," and the Kite Connect v3 documentation attributes the 6 AM
token expiry to a regulatory requirement.

Other jurisdictions impose their own multi-factor authentication expectations on trading
system access (e.g. MAS Notice on Cyber Hygiene for Singapore-regulated entities). Those
are not equivalent to the Indian OAuth-only mandate above and should not be described as
though they were — verify per jurisdiction rather than generalising.

*Nothing here is legal or compliance advice. Confirm current requirements and your
broker's terms of use before deploying.*

## Sources

- NSE circular NSE/INVG/67858, 05-May-2025 — https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf
- Kite Connect v3 documentation, "User / login flow" — https://kite.trade/docs/connect/v3/user/
- Kite Connect developer forum, "Mandatory TOTP for all Kite Connect apps" — https://kite.trade/forum/discussion/10391/
- Fyers support KB, "What is the function of the refresh token in FYERS?" — https://support.fyers.in/portal/en/kb/articles/what-is-the-function-of-the-refresh-token-in-fyers
- Upstox Developer API, "Authentication" — https://upstox.com/developer/api-documentation/authentication/
- Alpaca, "Authentication" — https://docs.alpaca.markets/docs/authentication
- ICICI Direct, "What is a Session Key & How to Generate It for Using Breeze API" — https://www.icicidirect.com/futures-and-options/api/breeze/article/what-is-a-session-key-and-how-to-generate-it-for-using-breezeapi
- IBC documentation (IBKR TWS/Gateway automation and 2FA limits) — https://github.com/IbcAlpha/IBC
