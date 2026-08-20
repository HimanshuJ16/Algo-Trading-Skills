# Bybit V5 Reference

All values below are from the Bybit V5 API documentation, consulted 2026-08-20. Bybit
changes endpoints and limits without a deprecation cycle; re-verify against the live
documentation before relying on a number here.

## Endpoints and signing

| Item | Value | Source |
|---|---|---|
| Protocol | Bybit API V5, REST/HTTPS | Integration Guidance |
| Base URL (mainnet) | `https://api.bybit.com` (also `https://api.bytick.com`) | Integration Guidance |
| Base URL (testnet) | `https://api-testnet.bybit.com` | Integration Guidance |
| Regional mainnet hosts | Separate hosts exist for NL, TR, KZ, GE, AE, EEA, ID, JP, BR | Integration Guidance |
| Signature (system-generated key) | HMAC-SHA256, **lowercase hex** | Create A Request |
| Signature (self-generated key) | RSA-SHA256, **base64** — not implemented in this skill | Create A Request |
| String to sign (GET) | `timestamp + api_key + recv_window + queryString` | Create A Request |
| String to sign (POST) | `timestamp + api_key + recv_window + jsonBodyString` | Create A Request |
| Query parameter ordering | **No ordering rule is specified.** The signed string must equal the transmitted string | Create A Request |
| JSON body formatting | **No canonical form is specified.** Bybit's own example signs `{"category": "option"}` with its spaces | Create A Request |

## Headers

| Header | Direction | Meaning |
|---|---|---|
| `X-BAPI-API-KEY` | request | API key. Required |
| `X-BAPI-TIMESTAMP` | request | UTC milliseconds. Required |
| `X-BAPI-SIGN` | request | Signature. Required |
| `X-BAPI-RECV-WINDOW` | request | Validity window in ms. Optional; default 5000 |
| `X-BAPI-SIGN-TYPE` | request | `2` for HMAC. Not listed as required in the V5 guide, but sent by the official `pybit` SDK; harmless and retained |
| `X-Referer` / `Referer` | request | Broker users only |
| `X-Bapi-Limit` | response | The current per-UID limit for this endpoint |
| `X-Bapi-Limit-Status` | response | Requests remaining against that limit |
| `X-Bapi-Limit-Reset-Timestamp` | response | Reset instant **only when the limit has been exceeded**; otherwise just the current timestamp |

## Timestamp acceptance window

Documented rule:

```
server_time - recv_window <= timestamp < server_time + 1000
```

The forward tolerance is fixed at 1000 ms and is **not** widened by `recv_window`, so a
fast clock cannot be compensated for by enlarging the window. Bybit states that a smaller
`recv_window` is more secure — it is the replay window — and recommends local device time
kept NTP-synchronised. `GET /v5/market/time` returns the server clock for verification.

The 60000 ms ceiling this skill enforces on `recv_window` is **not** a Bybit limit; it is a
local guard so an oversized replay window has to be chosen deliberately.

## Rate limits — two independent ceilings

| Limit | Scope | Value | Breach signal | Recovery |
|---|---|---|---|---|
| API rate limit | Per UID **and** per endpoint, rolling 1-second window | Per-endpoint, see Bybit's rate-limit table | `retCode 10006` "Too many visits" | Next window |
| IP rate limit | Per source IP, all endpoints | 600 requests per 5-second window | HTTP 403 "access too frequent"; `retCode 10018` | Terminate all HTTP sessions, wait **at least 10 minutes** for automatic unban |
| WebSocket connections | Per IP | ≤500 connections per 5 minutes; ≤1000 market-data connections | — | — |

The response headers describe the **first** limit only. Nothing in a successful response
warns that the IP ceiling is approaching.

Representative per-endpoint limits (linear/inverse contracts, standard UTA account) —
re-check against Bybit's table, which differs by category, account type and VIP tier:

| Endpoint | Limit |
|---|---|
| `POST /v5/order/create` | 10/s |
| `POST /v5/order/cancel` | 10/s |
| `POST /v5/order/cancel-all` | 10/s inverse, 1/s linear |
| `GET /v5/order/realtime` | 50/s |
| `GET /v5/position/list` | 50/s |

Bybit notes these are upgradable for some endpoints on application.

## Error codes relevant to integration

| Code | Meaning | Deterministic? |
|---|---|---|
| HTTP 401 | Missing or wrong key / auth params not in headers | Yes — do not retry |
| HTTP 403 | IP rate limit breached, GET sent with an empty JSON body, or a US IP | Yes — fix or wait out the ban |
| HTTP 429 | System-level frequency protection | No — retry with backoff |
| `10002` | Request time outside the acceptance window | Yes — fix the clock |
| `10003` | API key invalid; **check the key matches the domain** (mainnet / testnet / mainnet-demo / testnet-demo) | Yes |
| `10004` | Error sign — signature generation mismatch | Yes |
| `10005` | Permission denied — key lacks the permission | Yes |
| `10006` | API rate limit exceeded (per UID, per endpoint) | No — back off |
| `10010` | Unmatched IP — key's IP whitelist does not include the caller | Yes |
| `10018` | IP rate limit exceeded | No — stop all sessions, wait ≥10 min |
| `110072` | `orderLinkId` is duplicate (derivatives) | Yes — **this is the success case for a safe retry** |
| `170141` | Duplicate `clientOrderId` (spot) | Yes |

## Order identity

`orderLinkId` is a caller-chosen order id, **at most 36 characters**, and must be unique.
If both `orderId` and `orderLinkId` are supplied to an amend or cancel, Bybit gives
priority to `orderId`. Open-order ceilings: 500 active orders per symbol per account for
perpetuals and futures, of which at most 10 may be conditional.

## Sources

- Bybit V5 API — Integration Guidance: <https://bybit-exchange.github.io/docs/v5/guide>
- Bybit V5 API — Rate Limit Rules: <https://bybit-exchange.github.io/docs/v5/rate-limit>
- Bybit V5 API — Error Codes: <https://bybit-exchange.github.io/docs/v5/error>
- Bybit V5 API — Place Order: <https://bybit-exchange.github.io/docs/v5/order/create-order>
- Official Python SDK (`pybit`), signing implementation: <https://github.com/bybit-exchange/pybit>
- RFC 2104 (HMAC) and RFC 4231 (HMAC-SHA256 test vectors), used by the test oracle
