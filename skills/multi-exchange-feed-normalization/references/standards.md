# Broker & Framework Coverage — multi-exchange-feed-normalization

| Venue | Price Field | Quantity Field | Timestamp Field |
|---|---|---|---|
| Binance | `p` (string) | `q` (string) | `T` / `E` (ms epoch int) |
| Coinbase | `price` (string) | `size` (string) | `time` (ISO 8601 string) |
| Zerodha Kite | `last_price` (float) | `last_quantity` (int) | `last_trade_time` (string/datetime) |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with consolidated tape data standards, multi-venue market data architecture, and FIX Protocol data normalization standards.
