# Broker & Framework Coverage — market-data-snapshot-plus-delta-reconciliation

| Exchange / Feed | Sequence ID Field | Zero Qty Behavior | Re-Snapshot Trigger |
|---|---|---|---|
| Binance L2 `@depth` | `first_update_id` / `final_update_id` | `qty == 0` removes price level | `first_update_id > last_update_id + 1` |
| Coinbase Advanced L2 | `sequence_number` | `size == 0` removes price level | Discontinuous sequence number |
| Bybit L2 Stream | `seq` / `cts` | `v == 0` removes price level | Sequence gap |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with high-frequency market microstructure order book reconstruction and best execution quote verification standards.
