# Pre-Flight Checklist

## Environment & credentials
- [ ] Is the environment chosen explicitly (`is_simulation`), and does the base URL match — `gateway.saxobank.com/sim/openapi` for staging, `gateway.saxobank.com/openapi` for live?
- [ ] Are simulation and live application keys/secrets kept separate (Saxo does not share them across environments)?
- [ ] Is this a Developer Portal 24-hour token? If so, it is authorized for **simulation only** — it cannot be used live.
- [ ] Is the live OAuth2 access token refreshed proactively on a timer, given its 20-minute lifetime, rather than on the first HTTP 401?

## Instrument resolution
- [ ] Are instrument UICs resolved via `/ref/v1/instruments` prior to ordering, reading the UIC from the `Identifier` field?
- [ ] When the keyword search returns multiple rows, is the correct listing confirmed on `Symbol` + `ExchangeId` + `CurrencyCode` rather than defaulting to `Data[0]`?
- [ ] Are asset types (`FxSpot`, `Stock`, `ContractFutures`, `CfdOnStock`, `StockOption`) formatted exactly, with no attempt to trade `OptionRoot`?

## Order submission
- [ ] Does the order POST target `/trade/v2/orders`?
- [ ] Is `ManualOrder` present on every payload, and set to `false` for algorithmic orders?
- [ ] Is `OrderPrice` supplied for every `OrderType` other than `Market`?
- [ ] Is a random `ExternalReference` (≤ 50 chars) generated and **persisted before** the HTTP call?
- [ ] Is it understood and documented that `ExternalReference` is *not* an idempotency key — Saxo will accept a duplicate?
- [ ] Is the placement response treated as acceptance rather than a fill, with fill state confirmed separately?
- [ ] Does a 2xx response with no `OrderId` trigger reconciliation rather than a synthesised id or a resubmit?

## Reconciliation after an ambiguous submission
- [ ] Is `/port/v1/orders` queried and filtered on `ExternalReference` before any retry?
- [ ] Is a zero-match result treated as inconclusive (working orders only) and followed by a position check, not by an immediate resubmit?
- [ ] Are multiple matches on one `ExternalReference` detected and the surplus cancelled?

## Positions & P&L
- [ ] Is `FieldGroups=PositionBase,PositionView,DisplayAndFormat` sent on the positions query?
- [ ] Are `PositionId`/`NetPositionId` read from the row root and `Symbol` from `DisplayAndFormat` (neither is in `PositionBase`)?
- [ ] Is paging checked (`__count` vs rows returned, `__next`) so a truncated position list never feeds an exposure or drawdown check?
- [ ] Is `CalculationReliability` checked before any valuation feeds sizing or de-risking logic?
- [ ] Is portfolio P&L aggregated on `ProfitLossOnTradeInBaseCurrency` rather than summing `ProfitLossOnTrade` across currencies?

## Errors & rate limits
- [ ] Is HTTP 401 (refreshable) distinguished from 403 (entitlement) and from payload rejections?
- [ ] Is HTTP 429 backed off using the exhausted dimension's `X-RateLimit-<dimension>-Reset` value, in seconds?
- [ ] Is the tighter order-submission rate-limit bucket accounted for in burst sizing?
- [ ] Is every timeout routed to reconciliation instead of being treated as a rejection?
