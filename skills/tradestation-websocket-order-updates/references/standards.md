# Standards for TradeStation v3 Order Streaming

## Status of these requirements

Everything below is **TradeStation venue behaviour and this skill's engineering
standards** — not regulation. No securities regulator specifies stream frame
schemas, heartbeat intervals, or catch-up query formats. The "MUST" statements
are engineering rules this module enforces; the tables record what TradeStation's
own documentation and published v3 OpenAPI specification say. Do not present
either as a compliance obligation.

Verified against TradeStation's API documentation and published v3 OpenAPI
specification, 2026-09. Sources are listed at the bottom.

## Transport

| Item | Detail |
|---|---|
| Protocol | RFC2616 HTTP/1.1 chunked streaming — **not** WebSocket |
| Response headers | `Transfer-Encoding: chunked`, `Content-Type: application/vnd.tradestation.streams.v3+json` |
| Framing | "Do not use the HTTP chunks as application message delimiters." One chunk may carry several JSON objects; one object may span chunks; proxies re-chunk freely |
| Termination | Unlike a canonical HTTP/1.1 stream, TradeStation streams *can* terminate. On an error message "the HTTP client must terminate the HTTP Stream and end the HTTP Request lifetime." A delay before re-requesting is permitted |

Order and position streams are the ones served as `streams.v3+json`; market data
streams still use `streams.v2+json`.

## Endpoints

| Purpose | Method & path |
|---|---|
| Live order stream | `GET /v3/brokerage/stream/accounts/{accounts}/orders` |
| Live order stream, filtered | `GET /v3/brokerage/stream/accounts/{accounts}/orders/{ordersIds}` |
| Today's + open orders | `GET /v3/brokerage/accounts/{accounts}/orders` |
| Closed / historical orders | `GET /v3/brokerage/accounts/{accounts}/historicalorders` |

Base URL selects the environment, and nothing else does:

| Environment | Base URL |
|---|---|
| Live | `https://api.tradestation.com` |
| Simulator (paper) | `https://sim-api.tradestation.com` |

`accounts` is 1–25 account IDs, comma separated; TradeStation recommends batches
of 10.

**There is no `/v2/stream/orders` endpoint.** The published v2 specification
streams only barcharts, quotes and tickbars; its order endpoint is
`GET /v2/accounts/{account_keys}/orders`, not
`/v2/users/{user_id}/accounts/{account_id}/orders`. Order streaming is v3-only.

## Gap-reconciliation query parameters

This is the table that decides whether catch-up works at all.

| Endpoint | `since` | Page size | Pagination | Covers |
|---|---|---|---|---|
| `/orders` | **Not supported** | max & default 600 | `nextToken` | "Today's orders and open orders" |
| `/historicalorders` | **Required**, a *date*: `2006-01-13`, `01-13-2006`, `2006/01/13` or `01/13/2006`. Limited to 90 days prior to the current date | max & default 600 | `nextToken` | "Historical Orders … **except open orders**", sorted by time closed |

`nextToken` is "an encrypted token with a lifetime of 1 hour"; it is returned
with paginated results and used only in the immediately subsequent request.

Three consequences:

1. **A Unix timestamp is not a valid `since`.** Sub-day precision does not exist
   on this API. Catch-up re-reads whole days and relies on deduplication plus
   idempotent application to absorb the overlap.
2. **Neither endpoint alone covers an outage.** `/orders` omits orders closed
   before today; `/historicalorders` omits open orders. Query both and union the
   results.
3. **Ignoring `nextToken` truncates recovery at 600 orders** with no error.

## Frame schemas

| Frame | Shape | Meaning |
|---|---|---|
| Heartbeat | `{"Heartbeat": <int>, "Timestamp": "<RFC3339>"}` | "Sent to indicate that the stream is alive, although data is not actively being sent. A heartbeat will be sent after **5 seconds** on an idle stream." |
| Stream status | `{"StreamStatus": "EndSnapshot"}` | "When the initial snapshot is complete." Frames after this are live changes |
| Stream status | `{"StreamStatus": "GoAway"}` | "When the server is about to shut down … the stream will close because of server shutdown, and a new stream will need to be started by the client" |
| Error | `{"Error": ..., "Message": ..., "AccountID": ...}` | `Error` is one of `Forbidden`, `InternalServerError`, `ServiceUnavailable`, `GatewayTimeout`, `Failed`. `AccountID` accompanies `Forbidden` |
| Order | A v3 `Order` object (below) | A full cumulative snapshot of one order |

The 5-second idle heartbeat is what makes stall detection possible and is the
basis for this module's 15-second default threshold (three missed heartbeats).

## Order object fields that matter for fill accounting

The most common integration bug is reading fields that do not exist on this API.

| Field | Location | Notes |
|---|---|---|
| `OrderID` | top level | The only field this module treats as mandatory |
| `Status` | top level | Three-letter code; see the enum below |
| `StatusDescription` | top level | Human-readable form of `Status` |
| `FilledPrice` | top level | "At the top level, this is the average fill price. For expanded levels, this is the actual execution price" |
| `Legs[].ExecQuantity` | per leg | "Number of shares that have been executed" — **cumulative**, not a delta |
| `Legs[].QuantityOrdered` | per leg | Total ordered |
| `Legs[].QuantityRemaining` | per leg | "In a partially filled order, this is the number of shares or contracts that were unfilled" |
| `Legs[].ExecutionPrice` | per leg | Price at which execution occurred |
| `OpenedDateTime` / `ClosedDateTime` | top level | RFC3339 UTC; the correct anchors for a catch-up `since` |
| `RejectReason` | top level | Present when the order was rejected |

**There is no `FilledQuantity` field and no `AveragePrice` field.** Every numeric
arrives as a JSON *string*, so coercion must be defensive and money should be
handled as `Decimal`, not binary float.

## Order status enum

| Code | Meaning | Terminal? | Carries fills? |
|---|---|---|---|
| `ACK` | Received | no | no |
| `BRO` | Broken | yes | no |
| `CAN` | Canceled | yes | no |
| `CND` | Condition Met | no | no |
| `DON` | Queued | no | no |
| `EXP` | Expired | yes | no |
| `FLL` | Filled | yes | yes |
| `FLP` | Partial Fill (UROut) | yes | yes |
| `FPR` | Partial Fill (Alive) | **no** | yes |
| `LAT` | Too Late to Cancel | no | no |
| `OPN` | Sent | no | no |
| `OSO` | OSO Order | no | no |
| `OUT` | UROut | yes | no |
| `REJ` | Rejected | yes | no |
| `RJC` | Cancel Request Rejected | no | no |
| `RSN` | Replace Sent | no | no |
| `SUS` | Suspended | no | no |
| `TSC` | Trade Server Canceled | yes | no |
| `UCH` | Replaced | no | no |
| `UCN` | Cancel Sent | no | no |

The enum is TradeStation's; the "terminal" column is this skill's classification
of it, and the distinction that matters is `FPR` (still working, expect more
fills) versus `FLP` (remainder cancelled, order done). `LAT`, `RJC`, `UCN`,
`RSN` and `UCH` describe cancel/replace *attempts*, not the end of the order.

## Rate and concurrency limits

| Resource | Limit | Interval |
|---|---|---|
| Order Details / Accounts / Balances / Positions | 320 requests | rolling 5 minutes |
| Order Stream | 40 | concurrent connections |
| Order Stream by Order Id | 40 | concurrent connections |
| Positions Stream | 40 | concurrent connections |

Streaming connections consume concurrency slots, not the request quota;
TradeStation "recommend[s] using streaming services if available." Exceeding a
limit returns HTTP `429`. Relevant response headers: `X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `X-RateLimit-Reset`, `X-RateLimit-Resource`,
`X-Concurrency-Limit`, `X-Concurrency-Remaining`, `X-Concurrency-Resource`.

The 320/5-minute quota is the ceiling on how aggressively catch-up may poll: a
reconnect storm that re-queries orders on every attempt will exhaust it and turn
a recoverable outage into a `429` outage.

## Cross-broker note

Other venues solve the same reconnect-gap problem with different primitives —
IBKR with an open-order snapshot request, Binance Futures with a user-data-stream
listen key plus a REST trade query. Those mechanisms are **out of scope for this
skill and are not verified here**; consult each venue's own documentation and the
corresponding skill rather than porting the shapes above.

## Sources

- TradeStation, "HTTP Streaming" — https://api.tradestation.com/docs/fundamentals/http-streaming/ (framing, `EndSnapshot`, `GoAway`, error handling, content types)
- TradeStation, "Rate Limiting Overview" — https://api.tradestation.com/docs/fundamentals/rate-limiting/rate-limiting-overview (quotas, concurrency, headers)
- TradeStation, "Sim vs Live" — https://api.tradestation.com/docs/fundamentals/sim-vs-live (base URLs)
- TradeStation v3 OpenAPI specification, `https://api.tradestation.com/docs/specification/` — `StreamOrders`, `GetOrders`, `GetHistoricalOrders` operations and the `Order`, `OrderLeg`, `Status`, `Heartbeat`, `StreamStatus` and `ErrorResponse` schemas (endpoint paths, `since` format and 90-day limit, `pageSize`/`nextToken`, field names, status enum, 5-second heartbeat)
- TradeStation v2 specification — https://tradestation.github.io/api-docs/ (confirms no v2 order stream endpoint and the `/v2/accounts/{account_keys}/orders` path)
