---
name: saxo-bank-openapi-integration
description: >-
  Use when routing multi-asset orders through Saxo Bank OpenAPI, which identifies
  instruments by numeric UIC and requires an explicit AssetType on every order payload.
  Covers FX spot, equities, futures and options across simulation and live.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: saxo-bank, openapi, multi-asset, fx-spot, options-trading, uic-resolution
  brokers_frameworks: "Saxo Bank OpenAPI REST; OAuth2 Bearer Token; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when connecting algorithmic trading systems to Saxo Bank using their multi-asset OpenAPI REST endpoints. Saxo Bank OpenAPI enables cross-asset trading across FX Spot, Global Stocks, Contract Futures, Options, and Stock CFDs. Unlike single-asset brokers, Saxo Bank identifies instruments via numeric Unique Instrument Codes (UICs) and requires explicit `AssetType` declarations on order payloads.

## When NOT to Use

- **As a token/OAuth manager.** This skill consumes an already-valid `access_token`. Saxo's authorization-code flow, the 20-minute access-token lifetime, and refresh rotation belong upstream — see `headless-broker-auth-patterns` and `secrets-rotation-without-bot-downtime`.
- **As a streaming market-data feed.** These are REST polling endpoints. Real-time prices and position updates use Saxo's WebSocket streaming gateway (`live-streaming.saxobank.com` / `sim-streaming.saxobank.com`), which is a different transport with its own subscription lifecycle — see `websocket-reconnection-with-state-recovery`.
- **As an idempotency layer for order submission.** Saxo does not deduplicate on `ExternalReference` (see Pitfalls). Retry-safe submission is `order-placement-idempotency`.
- **As the risk gate.** `place_order` validates payload shape, not exposure. Pre-trade limits, drawdown halts, and kill switches must sit out-of-band — see `kill-switch-and-drawdown-circuit-breakers`.
- **For multi-leg option strategies.** Saxo routes those through the dedicated multi-leg strategy endpoints, not the single-order payload this client builds.

## Prerequisites

- Saxo OpenAPI OAuth2 Access Token (`access_token`) and Account Key (`account_key`).
- Target environment (`is_simulation`: True for `gateway.saxobank.com/sim/openapi`, False for live `gateway.saxobank.com/openapi`). Application key/secret are **not** shared between the two environments, and the Developer Portal's 24-hour token authorizes simulation only.
- An HTTP transport callable, injected as `http_fn(method, url, headers, body)`, returning `(status_code, body)` or `(status_code, body, headers)`. Returning headers is what enables rate-limit back-off.

## Workflow

1. **UIC Instrument Resolution**:
   - Query `/ref/v1/instruments?Keywords={ticker}&AssetTypes={asset_type}` to resolve ticker symbols to Saxo numeric UICs. The UIC is the `Identifier` field of each instrument summary.
   - Keyword search is a fuzzy match, not a lookup. Confirm `Symbol`, `ExchangeId` and `CurrencyCode` on the chosen row before routing — never take `Data[0]` on trust when several rows return.
2. **Multi-Asset Order Placement**:
   - Issue POST request to `/trade/v2/orders` specifying `AccountKey`, `Uic`, `AssetType`, `BuySell`, `Amount`, `OrderType`, `OrderDuration`, and `ManualOrder`.
   - Set `ManualOrder: false` for algorithmically generated orders; Saxo documents this field as mandatory for almost all applications.
   - Supply `OrderPrice` for every `OrderType` except `Market`. Attach a random `ExternalReference` (≤ 50 chars) so an ambiguous submission can be reconciled later.
   - Treat the response as *acceptance*, not a fill: it returns `OrderId` and any related `Orders`, and carries no execution status. If `OrderId` is absent, the order state is UNKNOWN — reconcile, do not resubmit.
3. **Position & P&L Retrieval**:
   - Query `/port/v1/positions?AccountKey={account_key}&FieldGroups=PositionBase,PositionView,DisplayAndFormat`.
   - Read `PositionId`/`NetPositionId` from the row root, instrument fields from `PositionBase`, valuation from `PositionView`, and `Symbol`/`Currency` from `DisplayAndFormat`.
   - Check `PositionView.CalculationReliability` before trusting any valuation, and aggregate portfolio P&L on `ProfitLossOnTradeInBaseCurrency`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unresolved UIC Identifiers**: Passing ticker string identifiers directly into order endpoints instead of performing UIC resolution first.
- **Incorrect AssetType Enum Values**: Passing string `"Equity"` instead of Saxo's exact enum string `"Stock"` or `"FxSpot"`.
- **Trading `OptionRoot` as an AssetType**: `OptionRoot` is an instrument-search concept used to enumerate a contract option space; it is not in Saxo's tradable `AssetType` enum and will not route. Resolve the contract's own UIC and trade it as `StockOption`, `FuturesOption`, `StockIndexOption`, or `FxVanillaOption`.
- **Assuming the 24-hour token behaves like a live session token**: The Developer Portal's one-day token is authorized for the **simulation environment only**. Live trading uses the OAuth2 authorization-code flow, whose access token expires after **20 minutes**; refresh on a timer, not after the first HTTP 401 lands mid-order-submission.
- **Retrying an order because the HTTP request timed out**: Saxo does not check `ExternalReference` for uniqueness and will not reject a repeated one — it is a correlation tag, not an idempotency key. A blind retry places a second order. Query `/port/v1/orders` filtered on your `ExternalReference` first, and remember that an *empty* result does not prove non-placement: that endpoint returns working orders only, so an order that already filled has left it. Check positions before concluding anything.
- **Reading `Symbol` or `PositionId` out of `PositionBase`**: `PositionBase` contains neither. `PositionId` and `NetPositionId` sit at the top level of each `Data` row, and `Symbol` is only returned inside `DisplayAndFormat` — which Saxo omits unless you request that `FieldGroups` value. Code that misses this silently produces blank position identifiers, breaking any close-by-`PositionId` logic.
- **Summing `ProfitLossOnTrade` across a multi-currency book**: That figure is denominated in the instrument's own currency. Adding USD and JPY P&L produces a meaningless number. Aggregate `ProfitLossOnTradeInBaseCurrency`, which Saxo has already converted to the account's base currency.
- **Ignoring `CalculationReliability`**: `PositionView` valuations carry a reliability marker. Sizing or de-risking off a valuation Saxo has not marked `"Ok"` propagates a stale or approximated price into risk decisions.
- **Treating a `Data` array as the complete set**: Saxo's collection endpoints are OData-paged (`$top` / `$skip`). A positions response whose `__count` exceeds the rows returned is a *partial* book — feeding it into an exposure or drawdown check silently understates risk. Page to exhaustion, or at minimum detect and refuse to act on a truncated result.
- **Treating HTTP 429 as a generic failure**: Saxo rate-limits per dimension and returns `X-RateLimit-<dimension>-{Limit,Remaining,Reset}`, where `Reset` is *seconds until that quota resets*. Back off using the exhausted dimension's `Reset`. Note the order-submission bucket is far tighter than the general session bucket — a burst loop will trip it.

## Verification

- Instantiate `SaxoBankOpenAPIClient`. Search instrument "EURUSD" (FxSpot) $\implies$ verify UIC 21 returned. Place limit buy order $\implies$ verify `OrderId` returned and `status` is `None` (Saxo returns no status on placement). Query positions $\implies$ verify `PositionId` read from the row root, `Symbol` from `DisplayAndFormat`, and unrealized P&L parsed in both instrument and base currency.
- Confirm a non-`Market` order without a price raises before any HTTP call, and that HTTP 401/429 raise `SaxoAuthError`/`SaxoRateLimitError` respectively.
- Run `python -m unittest discover -s skills/saxo-bank-openapi-integration/scripts`.

## Related Skills

- `broker-agnostic-adapter-interface`
- `sandbox-credential-leakage-prevention`
- `order-placement-idempotency`
- `multi-broker-rate-limit-handling`
- `multi-currency-pnl-and-fx-conversion`
