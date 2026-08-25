# Standards — graceful-degradation-to-polling-fallback

## 0. How to read this document

Section 1 is **protocol fact** from IETF RFCs. Sections 2–4 are **venue fact** taken from
official broker/exchange documentation, with the fetch date recorded. Sections 5–7 are
**engineering standards** — this repository's recommended practice, not requirements.

No regulator sets a feed-silence timeout, a polling interval or a stabilisation tick
count. Every number in Sections 5–7 is an operational default to calibrate. The venue
limits in Sections 2–4 are the opposite: they are published, enforced, and breaching them
has consequences up to a multi-day IP ban.

## 1. Protocol fact — why a dead socket stays silent

**TCP will not tell you.** RFC 1122 §4.2.3.6 (Requirements for Internet Hosts):
keep-alives are optional, "if keep-alives are included, the application MUST be able to
turn them on or off for each TCP connection, and they MUST default to off"; when enabled,
the probe interval "MUST be configurable and MUST default to no less than two hours". The
same section warns that an implementation "MUST NOT interpret failure to respond to any
specific probe as a dead connection". A silently frozen socket therefore has no
transport-layer deadline inside any trading horizon.
<https://www.rfc-editor.org/rfc/rfc1122>

**WebSocket does tell you, if you use it.** RFC 6455 §5.5.2: "A Ping frame may serve
either as a keepalive or as a means to verify that the remote endpoint is still
responsive." §5.5.3: "A Pong frame MAY be sent unsolicited. This serves as a
unidirectional heartbeat."
<https://www.rfc-editor.org/rfc/rfc6455>

This is the whole basis of the skill's central distinction: liveness is a transport
property, tick arrival is a market property, and a timeout on the second is not a
measurement of the first.

## 2. Venue coverage — liveness signal, fallback endpoint, dedup key

Fetched 2026-08-24 from official documentation. Verify before relying on any row: rate
limits and response schemas change without notice.

| Feed | Transport liveness signal | Suggested silence window | REST fallback | Timestamp in that response | Identity available |
|---|---|---|---|---|---|
| Zerodha Kite Connect | 1-byte heartbeat "every couple seconds" when idle | ~10 s | `GET /quote` (max 500 instruments) | `last_trade_time`, `"YYYY-MM-DD HH:MM:SS"`, **nullable** | None on `/quote` |
| Alpaca Market Data v2 | WebSocket Ping/Pong (RFC 6455) | Size to your client's ping interval | `GET /v2/stocks/trades/latest?symbols=` | `t`, RFC-3339, nanosecond precision | `i` (exchange trade id) |
| Binance Spot | Server ping every 20 s; disconnect if no pong within 1 minute | ≥ 45 s | `GET /api/v3/ticker/price` | **none — see §3** | None on this endpoint |

The silence windows are this repository's suggestion, derived from the liveness cadence in
column 2 (roughly twice the heartbeat interval). They are not venue-published values.

## 3. Correction — the Binance price ticker carries no timestamp

An earlier version of this table named `time` / `T` as the Binance deduplication field on
`GET /api/v3/ticker/price`. That is wrong, and it matters: the entire handover
deduplication design depends on a timestamp that this endpoint does not return.

The documented response is exactly two fields:

```json
{ "symbol": "LTCBTC", "price": "4.000002" }
```

Weight 2 for a single symbol, 4 for all symbols. `GET /api/v3/ticker/bookTicker` is
likewise timestamp-free (`symbol`, `bidPrice`, `bidQty`, `askPrice`, `askQty`).
<https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints>

Consequences for anyone polling Binance as a fallback:

- You cannot deduplicate a `/api/v3/ticker/price` snapshot against a timestamp watermark.
  Either stamp it locally and accept that it is a *quote* observation in a different clock
  domain from your trade stream, or poll an endpoint that does carry a time —
  `GET /api/v3/trades` returns `id` and `time` per trade and is the only one of the three
  that can also backfill the outage window.
- The weight-4 all-symbols form of `/ticker/price` is the correct shape for a
  multi-symbol fallback: one request covers the universe.

## 4. Venue fact — the published rate limits the fallback must respect

| Venue | Limit | Breach behaviour |
|---|---|---|
| Zerodha Kite Connect | `/quote` **1 req/second**; historical candle 3/s; order placement 10/s; all others 10/s | HTTP 429 |
| Alpaca | **200 requests/minute, per account** on the standard plan | HTTP 429 |
| Binance Spot | Weight-based per IP; `Retry-After` and `X-MBX-USED-WEIGHT-*` headers returned | 429, then 418 for continuing after a 429. "IP bans are tracked and scale in duration for repeat offenders, from 2 minutes to 3 days." |

Sources: <https://kite.trade/docs/connect/v3/exceptions/>,
<https://alpaca.markets/support/usage-limit-api-calls>,
<https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information>

Two conclusions follow directly, and neither is negotiable:

1. **A 500 ms polling interval is over the limit at Kite** (2 req/s against a documented
   1 req/s) and consumes 60% of an entire Alpaca account's minute budget for a single
   symbol.
2. **Per-symbol polling does not scale.** At one request per symbol per second, four
   symbols exceed Alpaca's 200/minute. Batch: Kite `/quote` takes 500 instruments, Alpaca
   `/v2/stocks/trades/latest` takes a `symbols` list, Binance `/ticker/price` returns
   every symbol for weight 4. Cross-venue budgeting is
   `multi-broker-rate-limit-handling`.

## 5. Engineering standard — the fallback is lossy by construction

*Recommended practice, not a requirement.*

A quote or ticker endpoint returns current state. It does not return the trades that
printed while the stream was down. Therefore:

- **Price-derived state recovers; volume-derived state does not.** Last price, bid/ask and
  mark-to-market are correct from the first successful poll. Volume, VWAP, trade counts,
  tick bars, volume bars and order-flow imbalance are all wrong across a handover.
- **Only a historical-trades endpoint backfills.** Alpaca `/v2/stocks/trades`, Binance
  `/api/v3/trades` or `/api/v3/aggTrades`. If you do not backfill, mark the affected
  indicators unusable for the duration of their lookback window rather than resuming them
  on a hole.
- **Report the hole.** `FeedStatus.last_degradation_gap_seconds` exists so the decision is
  explicit. A handover that reports nothing is a handover that silently corrupts state.

## 6. Engineering standard — deduplication needs identity

*Recommended practice, not a requirement.*

Venue timestamp resolution is routinely coarser than the trade rate, so a strict
`timestamp > watermark` test discards genuine trades:

- Kite Connect binary ticks carry `last_trade_time` and `exchange_timestamp` as **int32**
  fields; the REST quote renders them as `"YYYY-MM-DD HH:MM:SS"`. One-second resolution
  means `>` keeps the first trade of each second and drops every other one.
  <https://kite.trade/docs/connect/v3/websocket/>,
  <https://kite.trade/docs/connect/v3/market-quotes/>
- Alpaca timestamps are RFC-3339 with nanosecond precision. A nanosecond epoch (~1.8×10¹⁸)
  exceeds the 2⁵³ exactly-representable range of a float64, so two trades microseconds
  apart can compare equal after conversion. Keep them as integers, or deduplicate on `i`.
- Alpaca trades also carry `u` — `canceled`, `incorrect`, `corrected`. A correction can
  arrive with a timestamp at or behind the watermark and will be discarded by any
  watermark scheme. Handle corrections on their own path.

The rule this repository recommends: accept a tick whose timestamp is ahead of the
watermark, or equal to it with an identity not yet seen at that instant; count anything
behind the watermark as data loss rather than discarding it silently.

## 7. Engineering standard — thresholds and what they are for

*Recommended practice, not a requirement. Calibrate all of it.*

| Knob | Default in `scripts/feed_fallback_manager.py` | Why |
|---|---|---|
| `silence_timeout_seconds` | `3.0` | A placeholder only. Size it at ≥ 2× the venue heartbeat cadence; supply `heartbeat_interval_seconds` and the constructor enforces that. |
| `required_stabilization_ticks` | `5` | Anti-flapping. Must be ≥ 1; the run also resets whenever an inter-arrival gap exceeds the silence window. |
| `min_poll_interval_seconds` | `1.0` | The strictest of the three venues above (Kite's 1 req/s), not a universally safe value. |
| `max_consecutive_poll_failures` | `3` | Failed polls before declaring `BLIND_NO_DATA`. Throttled calls are explicitly not failures. |

**What this control covers:** transport death without a close event, failover to a
same-venue REST snapshot inside the published limit, duplicate suppression across the
handover, recency-gated handback, and an explicit blind signal.

**What it does not cover:** sequence gaps inside a live stream
(`sequence-number-gap-detection-for-feeds`), cross-vendor failover
(`vendor-outage-fallback-data-source-hierarchy`), order book state recovery
(`market-data-snapshot-plus-delta-reconciliation`), cross-symbol rate budgeting
(`multi-broker-rate-limit-handling`), and acting on the blind signal
(`capital-preservation-mode-for-degraded-conditions`).

## Regulatory note

No jurisdiction prescribes a feed-failover threshold. Firms in scope of MiFID II
algorithmic trading obligations have general real-time monitoring and resilience duties
under Commission Delegated Regulation (EU) 2017/589 (RTS 6) that a market data outage
plainly touches, but nothing there sets a number for any knob in this skill. Treat this
document as engineering practice and let your compliance function determine which regime
applies. Retention of feed-degradation event records is set by your applicable regime;
this skill asserts no retention period.

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category
rolls up across the full skill library.
