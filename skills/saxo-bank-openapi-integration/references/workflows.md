# Workflows for Saxo Bank OpenAPI Integration

Endpoint shapes and field locations below are cited in `references/standards.md`.

## 1. Environment and token selection

1. Pick the environment first, before any credential is loaded. `is_simulation=True` targets
   `gateway.saxobank.com/sim/openapi`; `False` targets the live gateway.
2. Do not carry credentials across environments: Saxo issues separate application keys and
   secrets per environment, and a Developer Portal 24-hour token is authorized for
   simulation only.
3. Live access tokens come from the OAuth2 authorization-code flow and expire after
   **20 minutes**. Refresh on a timer with margin. Reacting to the first HTTP 401 means
   the failure surfaces during an order submission, which is the worst possible moment.

## 2. UIC instrument resolution

1. `GET /ref/v1/instruments?Keywords={ticker}&AssetTypes={AssetType}`.
2. Read the UIC from each row's **`Identifier`** field.
3. Disambiguate. `Keywords` is a fuzzy search: a ticker may return several listings across
   exchanges and currencies, or nothing. Match on `Symbol`, then confirm `ExchangeId` and
   `CurrencyCode` against the intended venue. Routing to `Data[0]` on trust is how an order
   ends up on the wrong listing of a dual-listed name.
4. Check `TradableAs` when the instrument may be traded under more than one `AssetType`
   (an FX pair is commonly tradable as `FxSpot`, `FxForwards` and option types).
5. Cache resolved UICs. They are stable identifiers; re-resolving per order burns the
   reference-data rate-limit bucket for no benefit.

## 3. Order submission

1. Generate a random `ExternalReference` (≤ 50 characters) **before** submitting, and
   persist it with the intended order *before* the HTTP call. If the process dies mid-flight,
   that persisted reference is the only handle on a possibly-live order.
2. `POST /trade/v2/orders` with `AccountKey`, `Uic`, `AssetType`, `BuySell` (exactly `"Buy"`
   or `"Sell"`), `Amount`, `OrderType`, `OrderDuration.DurationType`, and `ManualOrder`.
3. Set `ManualOrder: false` for algorithmic orders. Saxo documents the field as mandatory
   for almost all applications; omitting it is a payload defect, not a stylistic choice.
4. Include `OrderPrice` for every order type except `Market`. Validate this client-side —
   a locally raised error costs nothing, a broker round-trip costs a rate-limit slot and
   latency.
5. On success, read `OrderId` and the related `Orders` array. There is **no status field**:
   acceptance is not a fill. Poll `/port/v1/orders` or subscribe to order updates for the
   working/filled transition.
6. If a 2xx arrives without an `OrderId`, the order state is UNKNOWN. Do not synthesise an
   identifier and do not resubmit — go to the reconciliation procedure.

## 4. Reconciling an ambiguous submission

Triggered by a timeout, connection reset, or a 2xx with no `OrderId`.

1. `GET /port/v1/orders?AccountKey={account_key}` and filter client-side on your
   `ExternalReference`. Saxo echoes it back on this endpoint.
2. One match ⇒ the order is working. Do not resubmit.
3. More than one match ⇒ a duplicate has already been placed. Saxo does not enforce
   uniqueness on `ExternalReference` and will not reject a repeat, so this is a real
   outcome, not a defensive hypothetical. Cancel the surplus.
4. **Zero matches does not mean "not placed."** `/port/v1/orders` returns working orders
   only; an order that already filled has left it. Before resubmitting, check
   `/port/v1/positions` (and closed positions) for a position consistent with the intended
   order. Resubmitting on an unchecked empty result is the standard route to a doubled
   position.

## 5. Position and P&L retrieval

1. `GET /port/v1/positions?AccountKey={account_key}&FieldGroups=PositionBase,PositionView,DisplayAndFormat`.
   `FieldGroups` is not optional in practice — omit `DisplayAndFormat` and every position
   comes back without a `Symbol`.
2. Parse per row:
   - `PositionId`, `NetPositionId` — **row root**, not `PositionBase`.
   - `Uic`, `AssetType`, `Amount`, `OpenPrice`, `Status` — `PositionBase`.
   - `CurrentPrice`, `ProfitLossOnTrade`, `ProfitLossOnTradeInBaseCurrency`,
     `CalculationReliability` — `PositionView`.
   - `Symbol`, `Currency` — `DisplayAndFormat`.
3. Check paging before trusting the list. These are OData collections: if `__count`
   exceeds the number of `Data` rows returned (or a `__next` link is present), the book is
   truncated. Page with `$top`/`$skip` to exhaustion — an exposure or drawdown check run on
   a partial position list understates risk in exactly the direction that hurts.
4. Gate on `CalculationReliability` before feeding any valuation into sizing or de-risking
   logic. Treat anything other than `"Ok"` as unconfirmed.
5. Aggregate portfolio P&L on `ProfitLossOnTradeInBaseCurrency`. `ProfitLossOnTrade` is in
   the instrument's own currency and summing it across currencies is arithmetic on
   mismatched units.

## 6. Error and rate-limit handling

| Response | Meaning | Correct reaction |
|---|---|---|
| 401 | Token missing, expired, invalid, or not entitled to the data | Refresh the access token and retry once. Do not mutate the payload. |
| 403 | Entitlement/permission failure | Refreshing will not help. Escalate — the application lacks a data group or write access. |
| 429 | Rate limit on some dimension | Back off for the exhausted dimension's `X-RateLimit-<dimension>-Reset` seconds. Never tight-loop. |
| 4xx (other) | Payload or state rejection | Classify before any retry. Do not blind-retry an order submission. |
| 5xx / timeout | Outcome unknown | Go to §4 reconciliation. Never treat a timeout as a rejection. |
