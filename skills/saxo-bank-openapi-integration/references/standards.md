# Standards for Saxo Bank OpenAPI Integration

Primary source: **Saxo Bank Developer Portal** — <https://www.developer.saxo/openapi/learn>
(specific pages cited per row below). This is a vendor API specification, not regulation;
nothing in this skill imposes a regulatory obligation. Saxo versions its service groups
independently and changes them without a global version bump — re-verify every row against
the portal before a production release.

## Environments

| Item | Value | Source |
|---|---|---|
| Simulation REST base | `https://gateway.saxobank.com/sim/openapi` | Environments |
| Live REST base | `https://gateway.saxobank.com/openapi` | Environments |
| Simulation streaming | `https://sim-streaming.saxobank.com/sim/oapi/streaming/ws` | Environments |
| Live streaming | `https://live-streaming.saxobank.com/oapi/streaming/ws` | Environments |
| Credential separation | "The application key and secret are not shared between the simulation and live environments." | Environments |
| Developer Portal 24-hour token | "One-day tokens obtained through the Developer Portal are only authorized for the simulation environment." Live access requires the full OAuth flow with live credentials. | Environments |

<https://www.developer.saxo/openapi/learn/environments>

## Authentication and token lifetime

| Item | Standard | Source |
|---|---|---|
| Authorization header | Every request MUST carry `Authorization: Bearer {access_token}`. | The Basics |
| Access token lifetime | "the refresh token is used to create a new access token when the old one expires after **20 minutes**." Refresh proactively on a timer. | Auth & Tokens |
| HTTP 401 | Returned when "the access token has expired", is absent, is invalid, or the caller is not entitled to the requested data. Distinguish it from other 4xx: it is recoverable by refreshing, not by changing the payload. | Status Codes |
| HTTP 403 | Entitlement/permission failure (missing application data group, missing write access, Granular User Access). Refreshing the token does **not** fix it. | Status Codes |

<https://openapi.help.saxo/hc/en-us/sections/4416631652113-Auth-Tokens> ·
<https://developer.saxobank.com/openapi/learn/status-codes>

## Rate limiting

| Item | Standard | Source |
|---|---|---|
| Exceeded quota | Returns "HttpStatus code **429 - Too Many Requests**." | Rate Limiting |
| Headers | `X-RateLimit-<dimension>-Limit`, `X-RateLimit-<dimension>-Remaining`, `X-RateLimit-<dimension>-Reset`, emitted per quota bucket. | Rate Limiting |
| `Reset` semantics | "Number of seconds until the quota is reset." It is a delay, not an absolute timestamp. | Rate Limiting |
| Buckets are independent | Quotas apply per dimension (application-daily, per-session-per-service-group, order submission). Order submission is materially tighter than the general session quota, so a burst of placements trips first. Back off on the dimension whose `Remaining` reached 0. | Rate Limiting |

<https://www.developer.saxo/openapi/learn/rate-limiting>

## Reference data — `GET /ref/v1/instruments`

| Item | Standard | Source |
|---|---|---|
| Instrument identifier | The UIC is returned as the **`Identifier`** field of the instrument summary, not as `Uic`. | Reference Data |
| Summary fields | `AssetType`, `CurrencyCode`, `Description`, `ExchangeId`, `GroupId`, `Identifier`, `SummaryType`, `Symbol`, `TradableAs`. | SummaryInfo schema |
| Query parameters | `Keywords`, `AssetTypes`, `ExchangeId`, `IncludeNonTradable`, plus OData `$top` / `$skip`. | Reference Data |
| Search semantics | `Keywords` is a fuzzy multi-instrument search. Several rows (or none) may return; the caller must disambiguate on `Symbol`/`ExchangeId`/`CurrencyCode`. | Reference Data |
| Worked example | EURUSD FxSpot resolves to `Identifier: 21` on `ExchangeId: "SBFX"`. Neighbouring FX UICs from the same block: EURAUD 12, GBPAUD 22, GBPCAD 23. | Reference Data examples |

<https://www.developer.saxo/openapi/learn/reference-data> ·
<https://developer.saxobank.com/openapi/referencedocs/ref/v1/instruments/get__ref/schema-summaryinfo>

## Order placement — `POST /trade/v2/orders`

| Item | Standard | Source |
|---|---|---|
| Endpoint | `POST {base}/trade/v2/orders` is the current documented order-placement endpoint. | Order Placement |
| Mandatory payload fields | `AssetType` & `Uic`, `Amount`, `OrderType`, `BuySell`, `OrderDuration` (with `DurationType`), `ManualOrder`. | Order Placement |
| `AccountKey` | "recommended" rather than formally mandatory — omitting it routes to the default account. Always send it explicitly on multi-account setups. | Order Placement |
| `OrderPrice` | "required for all types except market orders". | Order Placement |
| `ManualOrder` | "mandatory for most applications"; set `false` for algorithmically generated orders. | Order Placement |
| Response body | Returns `OrderId` plus an `Orders` array of related order ids. **No execution-status field.** Acceptance is not a fill. | Order Placement |
| `ExternalReference` | Optional client-defined label, max 50 characters, echoed on the placement response and on `/port/v1/orders`. | Client-defined order ID |
| `ExternalReference` is NOT an idempotency key | "The submitted value is *not* checked for uniqueness, and orders will **not be rejected** if a repeated `ExternalReference` is submitted." … "The client is responsible to ensure proper randomness to prevent duplicate references." | Client-defined order ID |

<https://www.developer.saxo/openapi/learn/order-placement> ·
<https://openapi.help.saxo/hc/en-us/articles/4418504615057-How-do-I-label-orders-with-a-client-defined-order-ID>

## AssetType, OrderType and OrderDuration enums

| Enum | Values relevant here | Note |
|---|---|---|
| `AssetType` | `FxSpot`, `Stock`, `ContractFutures`, `CfdOnStock`, `StockOption`, `FuturesOption`, `StockIndexOption`, `FxVanillaOption` | Saxo's full enum is far larger (Bond, Etf, FxForwards, CfdOnIndex, MiniFuture, Warrant, …). **`OptionRoot` is not in it** — it is a search/summary concept for enumerating a contract option space, and cannot be used on an order payload. |
| `OrderType` | `Market`, `Limit`, `Stop`, `StopIfTraded`, `StopLimit`, `TrailingStop`, `TrailingStopIfTraded` | Trailing variants need additional distance/step fields beyond `OrderPrice`; consult the order-placement reference for their exact names. |
| `OrderDurationType` | `DayOrder`, `GoodTillCancel`, `ImmediateOrCancel` (Saxo also defines `GoodTillDate`, `FillOrKill`, `AtTheOpening`, `AtTheClose`) | `GoodTillDate` additionally requires an expiry datetime and is not modelled by this client. |

## Portfolio — `GET /port/v1/positions`

| Item | Standard | Source |
|---|---|---|
| Query parameters | `AccountKey`, `ClientKey`, `AccountGroupKey`, `FieldGroups`, `NetPositionId`, `PositionId`, `WatchlistId`, `$top`, `$skip`. | Positions reference |
| `FieldGroups` | Selects which blocks are returned, "which helps limit the amount of data that has to be downloaded". Request `PositionBase,PositionView,DisplayAndFormat` explicitly. | Positions reference |
| `PositionId` / `NetPositionId` | Live at the **top level of each `Data` row**, alongside `PositionBase` and `PositionView` — not inside `PositionBase`. | Positions example response |
| `PositionBase` contents | `AccountId`, `Amount`, `AssetType`, `CanBeClosed`, `ClientId`, `ExecutionTimeOpen`, `OpenPrice`, `SourceOrderId`, `Status`, `Uic`, `ValueDate`. **No `Symbol`.** | Positions example response |
| `PositionView` contents | `CalculationReliability`, `ConversionRateCurrent`, `CurrentPrice`, `Exposure`, `ExposureCurrency`, `ProfitLossOnTrade`, `ProfitLossOnTradeInBaseCurrency`, `TradeCostsTotal`. | Positions example response |
| `DisplayAndFormat` contents | `Currency`, `Decimals`, `Description`, `Format`, `Symbol`. Only present when requested via `FieldGroups`. | Positions reference |
| P&L currency | `ProfitLossOnTrade` is in the instrument's own currency; `ProfitLossOnTradeInBaseCurrency` is the account-base-currency figure. Portfolio aggregation must use the latter. Worked check on the documented short 100,000 EURUSD position: open 1.13715, current 1.13273 ⇒ `ProfitLossOnTrade` 442 USD; × `ConversionRateCurrent` 0.882905 ⇒ 390.24 base currency, matching `ProfitLossOnTradeInBaseCurrency`. | Positions example response |

<https://www.developer.saxo/openapi/referencedocs/port/v1/positions>

### Unverified, treated defensively

`CalculationReliability` is documented as a `PositionView` field and appears as `"Ok"` in
Saxo's example responses, but the portal pages consulted do not enumerate its remaining
values or define them. This skill therefore applies a **defensive convention rather than a
documented rule**: only `"Ok"` counts as a confirmed valuation, and anything else (including
an absent field) is treated as unconfirmed so risk logic fails closed. Do not present that
as a Saxo-documented guarantee.
