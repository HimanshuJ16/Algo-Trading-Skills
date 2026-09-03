# Broker & Framework Coverage — tick-buffering-burst-handling

## Read this before using any number below to size a buffer

These figures are **published venue/vendor limits, not a substitute for measuring your
own feed.** Capacity must come from the peak rate observed on the feed you actually
consume (`BurstBufferManager.calculate_empirical_capacity`). A venue-wide consolidated
peak and the rate arriving at one retail WebSocket subscription differ by orders of
magnitude, and sizing against the wrong one is the failure this skill exists to prevent —
in either direction (an over-sized buffer just converts loss into stale-data latency).

## Documented feed rates and connection limits

| Feed | Published figure | Source | Currency |
|---|---|---|---|
| US equities — UTP SIP quotes (UQDF) | Peak **550,617 messages/sec**; recommended subscriber bandwidth 2.4 Gbps per stream | Nasdaq UTP Vendor Alert #2025-17, capacity test 14 Jun 2025 | 2025 through Q2 |
| US equities — UTP SIP trades (UTDF) | Peak **155,458 messages/sec** | Nasdaq UTP Vendor Alert #2025-17 | 2025 through Q2 |
| Zerodha Kite Connect v3 WebSocket | **3,000 instruments per connection**, **3 connections per API key**; packets are 8 B (LTP mode), 44 B (quote), 184 B (full, incl. depth) | Kite Connect v3 WebSocket streaming docs | Current |
| Binance spot WebSocket streams | `trade` / `aggTrade` / `bookTicker`: "Real-time"; individual symbol ticker: 1000 ms; kline: 1000 ms (`1s` interval) / 2000 ms (others); partial & diff depth: **1000 ms or 100 ms** | Binance Spot API — WebSocket Streams | Current |
| Binance WebSocket connection limits | Max **1024 streams per connection**; 5 incoming messages/sec; connection valid 24 h; server pings every 20 s, 60 s pong timeout | Binance Spot API — WebSocket Streams | Current |

**Not verified, therefore not tabulated.** No primary NSE or exchange-published figure was
found for peak NIFTY/BANKNIFTY tick rates on index expiry days, and the Kite Connect
documentation does **not** state a per-instrument update frequency or say whether ticks are
full tick-by-tick or throttled snapshots. Do not size an Indian-market buffer from a
second-hand ticks/second number — record a live expiry session and measure it.

## Sizing arithmetic these figures support

Peak rate alone does not size a buffer; memory does. With Kite full mode at 184 B per
packet, a 3,000-instrument subscription buffered at 2 s of lag tolerance and a measured
100 ticks/sec aggregate needs `ceil(100 × 2) = 200` slots — but the same 2 s tolerance
applied per-symbol across 3,000 symbols multiplies the bound by the symbol count. Bound
the **aggregate**, not just each symbol, and check the product against host RAM before
trusting a per-symbol number.

## Runtime primitive semantics

`collections.deque` documents "thread-safe, memory efficient appends and pops from either
side" — that guarantee covers *individual* operations, not compound check-then-act
sequences, which is why `scripts/burst_buffer.py` locks all state mutation. A bounded
deque also "discard[s] from the opposite end" once full, an implicit drop-oldest policy
applied by the data structure rather than by you (CPython `collections` documentation).

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

No regulator mandates a specific buffer size. Where dropped market data feeds order
decisions, best-execution and record-keeping regimes (e.g. MiFID II RTS 6 for algorithmic
trading systems, SEC Rule 15c3-5 pre-trade controls) make the *auditability* of the loss
the relevant obligation — which is why drop accounting here is exact counters, not a
best-effort log. Confirm the specific requirement against your own jurisdiction and
licence; nothing in this skill establishes one.

## Sources

- Nasdaq Trader — UTP Vendor Alert #2025-17, "Bandwidth Recommendations for the UTP SIP Services": https://www.nasdaqtrader.com/TraderNews.aspx?id=UTP2025-17
- Kite Connect v3 — WebSocket streaming: https://kite.trade/docs/connect/v3/websocket/
- Binance Spot API — WebSocket Streams: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
- CPython documentation — `collections.deque`: https://docs.python.org/3/library/collections.html
