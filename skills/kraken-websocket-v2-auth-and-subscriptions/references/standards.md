# Standards for Kraken WS v2 Auth & Subscriptions

## Status of these requirements

Everything below is **Kraken venue behaviour and this skill's engineering
standards** — not regulation. No securities regulator specifies WebSocket token
lifetimes or subscription schemas. The "MUST" statements are engineering rules
this engine enforces; the venue tables record what Kraken's own documentation
says. Do not present either as a compliance obligation.

Verified against Kraken's Spot API documentation, 2026-08.

## REST authentication

| Item | Detail |
|---|---|
| Endpoint | `POST /0/private/GetWebSocketsToken` |
| Signature | `API-Sign = Base64(HMAC-SHA512(url_path + SHA256(nonce + post_data), Base64Decode(private_key)))` |
| `url_path` | The path only, e.g. `/0/private/GetWebSocketsToken` — the part starting `/0/private`, not the full URL |
| Headers | `API-Key` (public key), `API-Sign` (computed signature) |
| Nonce | "An always increasing, unsigned 64-bit integer for each request"; must also appear as a payload parameter in the body |
| Nonce failure | `EAPI:Invalid nonce`. "Too many requests with invalid nonces … can result in temporary bans" |
| Key permission | The key needs **Other → Access WebSockets API**; without it the call returns `EGeneral:Permission denied` |
| Rate limit | Counts against the private REST counter: max 15 (Starter) / 20 (Intermediate) / 20 (Pro), decaying at −0.33 / −0.5 / −1 per second. Most calls cost 1. Exceeding it returns `EAPI:Rate limit exceeded` |
| Response | `{"token": "...", "expires": 900}` |

### The published test vector

Kraken documents a worked example, reproduced in this skill's tests as an
independently derived expected value:

| Field | Value |
|---|---|
| Private key | `kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==` |
| URI path | `/0/private/AddOrder` |
| Nonce | `1616492376594` |
| Payload | `nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25` |
| Expected `API-Sign` | `4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ==` |

If an implementation reproduces this signature, the HMAC construction is right.
This skill's `generate_kraken_rest_hmac_signature` does.

## Token lifetime — the exact wording

Kraken's REST reference for `GetWebSocketsToken`:

> "The token should be used within 15 minutes of creation, but it does not expire
> once a successful Websockets connection and private subscription has been made
> and is maintained."

So `expires: 900` is a **use-by window on the token**, not a TTL on the session.
This distinction drives the whole design: a maintained authenticated connection
needs no periodic refresh, and the freshness gate belongs at the point the token
is *used* to subscribe. Guidance that says "refresh every 12 minutes to avoid
mid-session disconnects" misstates the venue's behaviour and manufactures the
churn it claims to prevent.

## Endpoints

| Endpoint | Carries | Token |
|---|---|---|
| `wss://ws.kraken.com/v2` | Public channels | No |
| `wss://ws-auth.kraken.com/v2` | `executions`, `balances`, order-entry methods | Yes |
| `wss://ws-l3.kraken.com/v2` | `level3` | Yes |

Beta variants (`beta-ws`, `beta-ws-auth`) exist for pre-release testing.
`level3` is the trap: it is order book data, so a public/private split routes it
to the public host, but Kraken states "the `level3` channel is authenticated
(i.e. it requires an API token to subscribe)" and serves it from its own host.

Kraken closes an idle connection after roughly one minute; any request such as
`ping` keeps it alive, and an authenticated connection needs at least one
private subscription to stay open. Connection attempts are limited by Cloudflare
to approximately 150 per rolling 10 minutes per IP, with a 10-minute ban beyond
that — another reason not to resubscribe on a 15-minute timer.

## Channel registry (what the engine enforces)

| Channel | Endpoint | Token | Symbol | `depth` | Other params |
|---|---|---|---|---|---|
| `book` | public | — | required | 10, 25, 100, 500, 1000 (default 10) | `snapshot` (default true) |
| `ticker` | public | — | required | — | `event_trigger` (`bbo`/`trades`, default `trades`), `snapshot` (default true) |
| `trade` | public | — | required | — | `snapshot` (default **false**) |
| `ohlc` | public | — | required | — | `interval` ∈ {1, 5, 15, 30, 60, 240, 1440, 10080, 21600}, `snapshot` (default true) |
| `instrument` | public | — | not a param | — | `snapshot` (default true) |
| `executions` | ws-auth | required | not a param | — | `snap_orders` (default true), `snap_trades` (default **false**), `order_status` (default true), `ratecounter` (default false) |
| `balances` | ws-auth | required | not a param | — | `snapshot` (default true) |
| `level3` | ws-l3 | required | required | 10, 100, 1000 (default 10) | `snapshot` (default true) |

Note the two places the venue defaults differ from what a reader assumes:
`trade`'s `snapshot` and `executions`' `snap_trades` both default to *false*.
This engine emits `snap_orders` and `snap_trades` explicitly for `executions`
(both `True` by default in `KrakenWsV2SubscriptionSpec`), so the venue default
never applies to them — deliberate, because a reconnecting bot almost always
wants the fill snapshot. Every other optional flag is emitted only when the
caller sets it, leaving the venue default in force.

## Methods are not channels

`add_order` and `cancel_order` are top-level `method` values with `token` inside
`params`, optionally with `req_id`:

```json
{"method": "add_order", "params": {"order_type": "limit", "side": "buy",
 "order_qty": 1.25, "symbol": "BTC/USD", "limit_price": 37500,
 "token": "..."}, "req_id": 42}
```

Wrapping either in a subscribe frame is not a valid request. The engine returns
`INVALID_CHANNEL` naming the mistake instead of emitting one.

## Engineering standards enforced by the engine

| Standard | Rule | Enforced by |
|---|---|---|
| Credentials are explicit | No placeholder key or secret defaults; blank credentials raise at construction | `KrakenWsV2ManagerEngine.__init__` |
| Fail loudly on a bad secret | A secret that is not valid Base64 raises rather than being used raw as the HMAC key | `_decode_api_secret` |
| Sign what you send | The body must contain the nonce being signed; `url_path` must be a path | `generate_kraken_rest_hmac_signature` |
| Monotonic nonces | A nonce is never reissued, including across an NTP step backwards, under a lock | `KrakenNonceGenerator` |
| Registry is the authority | An unknown channel is rejected, never forwarded on the chance the venue accepts it | `CHANNEL_REGISTRY` |
| Per-channel routing | Endpoint, token requirement, symbol requirement and depth set come from the registry row | `KrakenV2ChannelSpec` |
| Use-by gate at point of use | Token age is checked on every frame build; `min(record expires, engine window)` wins | `_evaluate_token` |
| Graded token status | `TOKEN_REFRESH_REQUIRED` / `TOKEN_EXPIRED` / `TOKEN_CLOCK_SKEW` are distinct outcomes | `_evaluate_token` |
| No credential in logs | Audit text and log lines carry a token fingerprint, never the token | `redact_ws_token` |
| Fail loudly on NaN | Non-finite timestamps raise rather than passing a comparison that is `False` for NaN | `KrakenWsV2Error` |

## Sources

- Kraken, Spot REST Authentication (signature scheme, nonce rules, worked example) — https://docs.kraken.com/api/docs/guides/spot-rest-auth
- Kraken, Get Websockets Token (`expires`, use-by wording) — https://docs.kraken.com/api/docs/rest-api/get-websockets-token
- Kraken, Spot WebSocket Introduction (endpoints, inactivity timeout, connection rate limit) — https://docs.kraken.com/api/docs/guides/spot-ws-intro
- Kraken, WebSocket v2 `book` — https://docs.kraken.com/api/docs/websocket-v2/book
- Kraken, WebSocket v2 `ticker` — https://docs.kraken.com/api/docs/websocket-v2/ticker
- Kraken, WebSocket v2 `trade` — https://docs.kraken.com/api/docs/websocket-v2/trade
- Kraken, WebSocket v2 `ohlc` — https://docs.kraken.com/api/docs/websocket-v2/ohlc
- Kraken, WebSocket v2 `instrument` — https://docs.kraken.com/api/docs/websocket-v2/instrument
- Kraken, WebSocket v2 `executions` — https://docs.kraken.com/api/docs/websocket-v2/executions
- Kraken, WebSocket v2 `balances` — https://docs.kraken.com/api/docs/websocket-v2/balances
- Kraken, WebSocket v2 `level3` (authenticated, own host, depth set) — https://docs.kraken.com/api/docs/websocket-v2/level3
- Kraken, WebSocket v2 `add_order` — https://docs.kraken.com/api/docs/websocket-v2/add_order
- Kraken, WebSocket v2 `cancel_order` — https://docs.kraken.com/api/docs/websocket-v2/cancel_order
- Kraken, Spot REST Rate Limits — https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits
- Kraken, API key permissions — https://docs.kraken.com/exchange/guides/rest/api-keys
