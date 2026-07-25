# Real-Time Architecture Standards — multi-region-active-active-tick-ingestion

| Parameter | Specification | Description |
|---|---|---|
| Ingest Architecture | Active-Active Dual Region | Parallel market data feeds in 2+ regions |
| Deduplication Key | `MD5(symbol:seq:price:vol)` | Deterministic signature for tick matching |
| Signature Window TTL | 10.0 seconds | Retention window for deduplication cache |
| Arbitration Policy | First Arrival Wins | Fastest arriving region tick is forwarded |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
