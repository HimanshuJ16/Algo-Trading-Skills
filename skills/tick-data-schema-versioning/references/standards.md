# Real-Time Architecture Standards — tick-data-schema-versioning

| Schema Version | Timestamp Precision | Price Format | Extra Fields |
|---|---|---|---|
| Version 1 (V1) | Seconds (`float`) | Mid `price` | `symbol`, `volume` |
| Version 2 (V2) | Nanoseconds (`uint64`) | `bid`, `ask` | `exchange_id` |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
