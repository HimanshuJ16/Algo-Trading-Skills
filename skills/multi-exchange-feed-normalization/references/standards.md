# Venue Field Mapping — multi-exchange-feed-normalization

All field names below are taken from the venues' own published specifications; see
Sources at the end. Where this table disagrees with a third-party wrapper, trust the
venue.

## Trade-print field mapping

| Venue / channel | Symbol | Price | Trade size | Timestamp | Side field |
|---|---|---|---|---|---|
| Binance spot `trade` / `aggTrade` | `s` | `p` (string) | `q` (string) | `T` trade time, `E` event time (ms epoch int by default) | `m` — "Is the buyer the market maker?" (bool) |
| Coinbase Exchange `matches` | `product_id` | `price` (string) | `size` (string) | `time` (ISO-8601 string) | `side` — **maker** order side |
| Coinbase Advanced Trade `market_trades` | `product_id` | `price` (string) | `size` (string) | `time` (ISO-8601 string) | `side` — **maker** order side |
| Zerodha Kite WebSocket tick | `instrument_token` | `last_price` (float) | `last_traded_quantity` (int) | `last_trade_time`, `exchange_timestamp` (naive `datetime`) | *none* |
| Zerodha Kite REST quote | `tradingsymbol` | `last_price` (float) | `last_quantity` (int) | `last_trade_time` | *none* |

## Aggressor-side derivation

`UnifiedTick.side` is the **aggressor (taker)** side at every venue. The venues do not
share a convention, so the field is derived, never copied:

| Venue | Venue field | Meaning | Derivation |
|---|---|---|---|
| Binance | `m = true` | buyer was the maker; a resting bid was hit | aggressor = `SELL` |
| Binance | `m = false` | buyer was the taker | aggressor = `BUY` |
| Coinbase | `side = "buy"` | **maker** bought; a resting bid was hit | aggressor = `SELL` |
| Coinbase | `side = "sell"` | **maker** sold; a resting offer was lifted | aggressor = `BUY` |
| Zerodha | — | Kite ticks carry no aggressor flag | `UNKNOWN` |

Binance and Coinbase encode the *same* economic event (a resting bid being hit) as
`m=true` and `side="buy"` respectively. Both must normalize to `SELL`. Copying
Coinbase's field through while deriving Binance's from `m` puts the two venues on
opposite conventions and sign-flips any cross-venue order-flow imbalance.

Do not infer an aggressor side for Zerodha from tick direction. An uptick describes the
price relative to the previous print, not which counterparty crossed the spread.

## Timestamp units

Binance serves millisecond epochs by default. The documented `timeUnit=MICROSECOND`
(or `timeUnit=microsecond`) URL parameter switches the same streams to microseconds —
which is why unit inference must test disjoint magnitude bands rather than a single
`> 1e11` threshold, and why a per-venue unit can be declared explicitly via
`register_parser(..., timestamp_unit=...)`.

Bands used, derived from a plausible epoch window of 2001-09-09 (`1e9` s) to
2100-01-01 (`4102444800` s). They are three decades apart, so at most one matches:

| Unit | Accepted numeric range |
|---|---|
| seconds | `1e9` … `4.102e9` |
| milliseconds | `1e12` … `4.102e12` |
| microseconds | `1e15` … `4.102e15` |
| nanoseconds | `1e18` … `4.102e18` |

`pykiteconnect` constructs `last_trade_time` and `exchange_timestamp` with
`datetime.fromtimestamp(epoch)`, producing a **naive** datetime in the recording host's
local timezone. Pass `naive_timestamp_tz=None` when consuming those objects directly so
the conversion inverts that construction exactly; any other setting reinterprets the
wall-clock reading in a different zone.

## Price sign

Non-positive prices are rejected by default. This is correct for every venue in the
table above, where a zero or negative price means a corrupt payload. It is not
universally correct: CME enabled negative pricing on Globex for certain energy products
in April 2020, and the NYMEX WTI May-2020 contract settled at **-$37.63/bbl** on
2020-04-20. Set `allow_non_positive_price=True` only for instruments where that is
genuinely possible.

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category
rolls up across the full skill library.

## Scope boundary

This skill covers payload-to-schema mapping only. It holds no cross-message state and
therefore performs no sequencing, deduplication, or gap detection; see
`sequence-number-gap-detection-for-feeds`. It does not correct venue clock offsets; see
`clock-skew-correction-for-tick-timestamps`. Normalizing vendor data does not confer
redistribution rights; see `market-data-entitlement-and-licensing-per-venue`.

## Sources

- Binance, *WebSocket Streams* (spot): `trade` and `aggTrade` payload definitions,
  including `"m": "Is the buyer the market maker?"` and the `timeUnit=MICROSECOND`
  parameter — <https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md>
- Coinbase, *Exchange WebSocket Channels*, `matches` channel: "The `side` field
  indicates the maker order side. If the side is `sell` this indicates the maker was a
  sell order" — <https://docs.cdp.coinbase.com/exchange/websocket-feed/channels>
- Coinbase, *Advanced Trade WebSocket Channels*, `market_trades` channel — the trade
  `side` refers to the maker's side —
  <https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-channels>
- Zerodha, `pykiteconnect` ticker implementation: tick dictionary keys and the
  `datetime.fromtimestamp(...)` construction of `last_trade_time` /
  `exchange_timestamp` —
  <https://github.com/zerodha/pykiteconnect/blob/master/kiteconnect/ticker.py>
- CFTC, *Interim Staff Report on Trading in NYMEX WTI Crude Oil Futures Contract
  Leading up to, on, and around April 20, 2020* — CME's enablement of negative pricing
  and the -$37.63 settlement — <https://www.cftc.gov/media/5296/InterimStaffReportNYMEX_WTICrudeOil/download>
