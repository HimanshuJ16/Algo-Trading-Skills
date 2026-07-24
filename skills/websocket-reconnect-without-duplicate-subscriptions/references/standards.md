# Broker & Framework Coverage — websocket-reconnect-without-duplicate-subscriptions

| Broker / Streaming Protocol | Resubscription & Deduplication Specs |
|---|---|
| Fyers WebSocket API v3 | Maximum 500 symbols per WebSocket connection. |
| Zerodha Kite Connect Ticker | Binary ticker mode; requires explicit resubscribe call after reconnect. |
| IBKR TWS / Gateway API | Order/market data stream reconnection and sequence number tracking. |
| Alpaca Real-Time Stream | JSON streaming protocol requiring re-auth + resubscribe on disconnect. |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with real-time market data continuity standards, audit trail sequence integrity, and broker streaming API connectivity compliance.
