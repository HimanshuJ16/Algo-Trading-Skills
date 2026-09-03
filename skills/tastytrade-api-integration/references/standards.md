# Broker Integration Standards — tastytrade-api-integration

Every row below is sourced. Where Tastytrade publishes no figure, the row says so
rather than quoting a community number as a contract.

## Environments

| Parameter | Specification | Notes |
|---|---|---|
| Production base URL | `https://api.tastyworks.com` | Live money. |
| Certification base URL | `https://api.cert.tastyworks.com` | Separate credentials and balances; **not** a production mirror. |
| `Accept-Version` header | `20251101` | Sent on production only — the sandbox does not accept it. |
| `User-Agent` header | `<product>/<version>` | **Required.** Any other shape returns `401` from the edge proxy. |
| `Content-Type` / `Accept` | `application/json` | Request and response bodies are JSON. |

## Authentication (OAuth2)

| Parameter | Specification |
|---|---|
| Retired flow | `POST /sessions` (email + password → `session-token`) — **discontinued 2025-12-01**. |
| Token endpoint | `POST /oauth/token` |
| Grant | `{"grant_type": "refresh_token", "client_secret": "...", "refresh_token": "..."}` |
| Response | `{"access_token": "...", "token_type": "Bearer", "expires_in": 900}` |
| Access token lifetime | **15 minutes**, sent on every request. |
| Refresh token | Long-lived; generated at OAuth Applications → Manage → Create Grant. Revocable. |
| Auth header | `Authorization: Bearer {access_token}` (the retired flow used a bare `Authorization: {session-token}`). |
| Failed-login protection | Too many failed login attempts blocks the source IP outright, typically for **8 hours**, during which requests time out. |

## Order endpoints

| Operation | Path |
|---|---|
| Accounts | `GET /customers/me/accounts` |
| Option chain | `GET /option-chains/{symbol}` |
| Pre-trade validation | `POST /accounts/{account}/orders/dry-run` |
| Order placement | `POST /accounts/{account}/orders` |
| Working orders | `GET /accounts/{account}/orders/live` |
| Single order | `GET /accounts/{account}/orders/{order_id}` |
| Cancel | `DELETE /accounts/{account}/orders/{order_id}` |
| Positions | `GET /accounts/{account}/positions` |
| Contingent groups (out of scope here) | `/accounts/{account}/complex-orders` — OCO/OTOCO, distinct from a multi-leg order. |

### Order payload

| Field | Specification |
|---|---|
| `order-type` | `Limit`, `Market`, `Marketable Limit`, `Stop`, `Stop Limit`, `Notional Market`. This skill models `Limit` and `Market`. |
| `time-in-force` | `Day`, `GTC`, `GTD`, `Ext`, `Ext Overnight`, `GTC Ext`, `GTC Ext Overnight`, `IOC`. |
| `price` | Absolute magnitude. **The API does not accept negative numbers.** Absent on `Market` orders, which have no price field. |
| `price-effect` | `Debit` (account pays) or `Credit` (account receives). Carries the direction that the sign would otherwise carry. |
| `legs[].instrument-type` | `Equity`, `Equity Option`, `Future`, `Future Option`, and others including `Cryptocurrency`, `Index`, `Warrant`. |
| `legs[].action` | `Buy to Open`, `Sell to Open`, `Buy to Close`, `Sell to Close` (plus `Buy`/`Sell` for non-option instruments). |
| `legs[].quantity` | Positive; whole contracts for options and futures. |
| `external-identifier` | Optional caller-supplied tag, echoed back on the order. **Reconciliation aid only — Tastytrade documents no server-side de-duplication on it.** |

### Response envelopes

| Shape | Meaning |
|---|---|
| `{"data": {"order": {...}, "buying-power-effect": {...}, "fee-calculation": {...}, "warnings": [...], "errors": [...]}}` | Order placement and dry run. `warnings` can appear on a 2xx. |
| `{"data": {"items": [...]}}` | Collections (accounts, positions, live orders). |
| `{"error": {"code": "...", "message": "...", "errors": [...]}}` | Errors. Nested entries may use `code`/`message` or `domain`/`reason`. |

## Symbology

| Instrument | Format | Example |
|---|---|---|
| Equity option | OCC, exactly **21 characters**: 6-char space-padded root, `YYMMDD`, `C`/`P`, strike × 1000 zero-padded to 8 digits. | `AAPL  240816C00200000` |
| Future option | Tastytrade's own format — **not** OCC. Resolve from the future-option chain. | `./ESU4 EW4Q4 240823C5750` |
| Streamer symbol | Distinct from the order symbol; used for quote subscriptions only. | `./EW4Q24C5750:XCME` |

Consequences of the 8-digit strike field: the maximum representable strike is
`99999.999`, and the increment is 1/1000. A strike with finer precision has no
representation — it must be rejected, not rounded, because rounding produces a
well-formed symbol naming a different contract.

## Idempotency

Tastytrade publishes **no client-supplied idempotency key** for order placement.
An ambiguous submission (transport failure, 408/425/429/5xx, or a 2xx with no
order id) must be resolved by reading `GET /accounts/{account}/orders/live` and
matching on `external-identifier` — never by resubmitting.

## Rate limits

Tastytrade does not publish a general request-rate limit in its developer
documentation. Community clients self-throttle at roughly 2 requests/second;
that is a convention, **not** a documented contract, and must not be quoted as
one. The only officially documented throttle is the failed-login IP block above.

## Sources

- tastytrade developer FAQ — User-Agent `<product>/<version>` requirement and the
  401 it causes; "Access tokens last 15 minutes and must be sent with every
  request in the Authorization header"; 8-hour IP block on repeated failed
  logins. <https://developer.tastytrade.com/faq/>
- tastytrade OAuth2 guide — replacement authentication flow and personal OAuth
  applications. <https://developer.tastytrade.com/api-guides/oauth/>
- tastytrade session-token discontinuation announcement — "tastytrade
  session-token authentication will be discontinued on December 1st, 2025."
  <https://github.com/tastyware/tastytrade/issues/269>
- tastyware/tastytrade reference client — `POST /oauth/token` body and response
  fields, `Authorization: Bearer`, base URL and `Accept-Version` constants, order
  and dry-run paths, response envelopes, error envelope parsing, the enum value
  sets, and `PriceEffect` ("the API doesn't use negative numbers"; negative maps
  to `Debit`). <https://github.com/tastyware/tastytrade>
- tastytrade instruments API guide — OCC equity option symbol layout and the
  distinct future-option and streamer symbol formats.
  <https://developer.tastytrade.com/api-guides/instruments/>
- OCC option symbol standard — 21-character structure.
  <https://en.wikipedia.org/wiki/Option_symbol>
