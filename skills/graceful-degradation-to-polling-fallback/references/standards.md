# Broker & Framework Coverage — graceful-degradation-to-polling-fallback

| Market Data Feed | Silence Timeout Limit | REST Fallback Endpoint | Handover Dedup Field |
|---|---|---|---|
| Zerodha Kite WebSockets | $3.0\text{s}$ | `GET /quote` | `last_trade_time` |
| Alpaca Market Data Stream | $5.0\text{s}$ | `GET /v2/stocks/trades/latest` | `timestamp` |
| Binance Spot WebSockets | $3.0\text{s}$ | `GET /api/v3/ticker/price` | `time` / `T` |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with high-availability trading systems, mission-critical market data resiliency, and fault-tolerant architecture standards.
