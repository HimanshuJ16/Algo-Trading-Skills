# Broker & Framework Coverage — tick-buffering-burst-handling

| High-Frequency Feed | Peak Tick Rate Specifications |
|---|---|
| US Equities (SIP / Direct Feeds) | Up to 100,000+ ticks/sec during volatility bursts. |
| Indian Derivatives (NSE NIFTY/BANKNIFTY) | Up to 5,000+ ticks/sec on index expiry days. |
| Crypto Spot / Derivatives (Binance, Bybit) | Up to 20,000+ ticks/sec during liquidation cascades. |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with real-time market data processing SLAs, data loss auditability, and memory resource management standards.
