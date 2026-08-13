# Standards for Alternative Data Feature Engineering

| Concept | Description | Rule |
|---|---|---|
| **Point-in-Time (PIT)** | The exact time a piece of data became actionable. | All backtesting models must strictly use PIT timestamps, never Event timestamps. The `knowledge_timestamp` invariant `knowledge_timestamp <= trading_time` must hold for every served (non-`UNKNOWN`) aligned value. |
| **Look-ahead Bias** | Leaking future information into the past. | Forward-filling is only permitted *after* the PIT publication lag has been applied. |
| **Publication Lag** | The delay from event occurrence to vendor publication. | `publication_lag` must be **non-negative**. A negative lag makes the knowledge timestamp precede the event and silently re-introduces look-ahead; the integrator rejects it. The lag must be a defensible, vendor-confirmed constant per source. |
| **Data Revision** | Vendors occasionally update past data files. | A restatement is modelled as an **appended** PIT fact with `knowledge_timestamp = revised_date + publication_lag`. The original data is served until the exact `revised_date` (plus its lag) is reached in the simulation; restatements are never overwritten onto the original fact. |
| **MNPI Source Classification** | Whether a source could carry Material Non-Public Information. | Every source must have a recorded MNPI classification before ingest. MNPI-bearing sources require handling controls or rejection. Gate: `insider-trading-controls-for-alternative-data-usage`. |
| **PII / Anonymization** | Personal data embedded in raw alternative datasets. | PII must be scrubbed before ingest. Panel/aggregated data must meet a >= 50-contributor threshold per cell to prevent small-cell re-identification. |
| **Licensing / Usage Restrictions** | Contractual limits on how data may be used. | The vendor contract must cover the intended use (e.g., live trading, not research-only) and jurisdiction. Sources whose contract is research-only must not feed a live model. Gate: `data-vendor-contractual-usage-restriction-tracking`. |
| **Freshness / Staleness SLA** | How old a value may be before it is unreliable. | Each source should have a `max_age` TTL. When `age > max_age`, the aligned value is reported as `None` with `staleness_state = STALE`; it is never silently forward-filled indefinitely on a vendor outage/lapse. |
| **Timezone Convention** | How datetimes are represented internally. | All datetimes (`event_timestamp`, `revised_date`, `trading_times`) are **naive UTC**. Timezone-aware datetimes are rejected at ingest and at alignment to avoid a mid-loop `TypeError` and UTC/ET offset confusion that would silently shift knowledge times. |
| **Schema Version Contract** | Vendor payload format versioning. | Each payload carries a `schema_version`; the integrator asserts it matches the configured expected version per source and rejects on drift, preventing silent vendor-change breakage of the PIT mapping. |

## Category
`financial-ml`
