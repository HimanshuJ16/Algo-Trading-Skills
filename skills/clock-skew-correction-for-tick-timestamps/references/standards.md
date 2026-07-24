# Broker & Framework Coverage — clock-skew-correction-for-tick-timestamps

| Exchange / Feed | Timestamp Resolution | Typical Skew Range | Clock Sync Protocol |
|---|---|---|---|
| CME Globex | Nanosecond (`ns`) | $< 1\text{ms}$ | PTP (IEEE 1588) |
| Nasdaq ITCH | Nanosecond (`ns`) | $< 5\text{ms}$ | PTP (IEEE 1588) |
| Crypto WebSockets (Binance/Kraken) | Millisecond (`ms`) | $10\text{ms} - 500\text{ms}$ | NTP (RFC 5905) |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with MiFID II RTS 25 clock synchronization requirements (100-microsecond accuracy for HFT, 1-millisecond for automated trading).
