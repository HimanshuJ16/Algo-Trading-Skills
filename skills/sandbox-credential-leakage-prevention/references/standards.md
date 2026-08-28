# Standards for Sandbox Credential Leakage Prevention

## Endpoint allow-list — shipped defaults

Hostnames below were verified against vendor documentation in **August 2026**. They
are a starting point, not a permanent contract: venues add and retire hosts without
changing their API version. Re-check against the cited pages before relying on them,
and use `iter_declared_endpoints()` to dump the shipped list for diffing.

| Broker | Environment | Permitted endpoint (host [+ path prefix]) | Source |
|---|---|---|---|
| Alpaca | SANDBOX | `paper-api.alpaca.markets` | Alpaca *Authentication* / *Paper Trading* docs |
| Alpaca | PRODUCTION | `api.alpaca.markets` | Alpaca *Authentication* docs |
| Binance | SANDBOX | `testnet.binance.vision` (spot) | Binance Spot testnet *General API Information* |
| Binance | SANDBOX | `demo-fapi.binance.com`, `demo-dapi.binance.com` | Binance USD-M / COIN-M Futures *General Info* |
| Binance | SANDBOX | `testnet.binancefuture.com` (legacy, still served) | Binance Futures docs, alternate testnet host |
| Binance | PRODUCTION | `api.binance.com`, `api-gcp.binance.com`, `api1`–`api4.binance.com` | Binance Spot *General API Information* |
| Binance | PRODUCTION | `fapi.binance.com`, `dapi.binance.com` | Binance Futures *General Info* |
| Binance | PRODUCTION | `data-api.binance.vision` (market data only) | Binance Spot *General API Information* |
| Saxo Bank | SANDBOX | `gateway.saxobank.com/sim/openapi`, `sim-streaming.saxobank.com` | Saxo OpenAPI *Environments* |
| Saxo Bank | PRODUCTION | `gateway.saxobank.com/openapi`, `live-streaming.saxobank.com` | Saxo OpenAPI *Environments* |

### Sources

- Alpaca, *Authentication* — <https://docs.alpaca.markets/us/v1.1/docs/authentication-1>
  (live `https://api.alpaca.markets`, paper `https://paper-api.alpaca.markets`;
  headers `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY`).
- Alpaca, *Paper Trading* — <https://docs.alpaca.markets/docs/paper-trading>
  ("Your paper trading account will have a different API key from your live account.")
- Binance, *Spot REST — General API Information* —
  <https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information>
- Binance, *Spot Testnet — General API Information* —
  <https://developers.binance.com/docs/binance-spot-api-docs/testnet/rest-api/general-api-information>
- Binance, *USD-M Futures — General Info* —
  <https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info>
- Saxo Bank, *OpenAPI Environments* — <https://www.developer.saxo/openapi/learn/environments>

## Two facts that shape the matching rules

**1. Saxo separates environments by path, on a shared hostname.**
Simulation is `https://gateway.saxobank.com/sim/openapi` and live is
`https://gateway.saxobank.com/openapi`. A hostname-only allow-list treats the two as
the same endpoint. Path prefixes are therefore part of the rule, and the request path
is normalised (`posixpath.normpath`) before comparison so
`/sim/openapi/../../openapi/...` is judged as the live path it resolves to.

**2. Binance uses one registrable domain for both a production and a testnet host.**
`data-api.binance.vision` serves production market data; `testnet.binance.vision`
serves the spot testnet. No substring of the domain separates them, which is why the
guard compares the full hostname exactly.

## API key prefixes — advisory, not authoritative

| Broker | Sandbox prefix | Production prefix | Documented by vendor? |
|---|---|---|---|
| Alpaca | `PK` | `AK` | **No.** Widely observed, but Alpaca's *Authentication* page specifies no key format. |
| Binance | — | — | No prefix exists. Keys are HMAC-SHA256, RSA, or Ed25519 credentials with no environment marker. |
| Saxo Bank | — | — | No prefix exists. Access is via OAuth2 bearer tokens; 24-hour Developer Portal tokens are authorised for simulation only, and application key/secret pairs are not shared between environments. |

Consequences encoded in `credential_guard.py`:

- A key carrying the **opposing** environment's prefix is a hard `SecurityViolationError`.
  This direction is a high-confidence positive signal.
- A key matching **none** of the expected prefixes produces a `WARNING` only. Blocking
  on it would reject every Binance and Saxo credential, and would hard-code an
  undocumented Alpaca convention as if it were a contract.
- The **endpoint allow-list is the authoritative control.** Prefix checking is
  defence in depth layered on top of it.

## Transport requirements enforced

| Check | Rationale |
|---|---|
| Scheme must be `https` | An `http://` broker call transmits `APCA-API-KEY-ID` (or equivalent) in cleartext. |
| No userinfo in URL | `https://api.alpaca.markets@evil.example/` reads as one host and resolves to another; and credentials belong in headers, never in a URL. |
| Hostname must be present and match exactly | Substring matching accepts `api.alpaca.markets.attacker.example`. |
| Port must be absent or 443 | Broker gateways are served on 443; any other port is an unrecognised destination. |
| URL redacted before logging | Binance signs REST calls with `&signature=<hmac>` in the query string; OAuth flows carry tokens as query parameters. |
