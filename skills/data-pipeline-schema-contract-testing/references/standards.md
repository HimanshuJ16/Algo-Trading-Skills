# Standards for Data Pipeline Schema Contract Testing

| Metric | Engineering Standard |
|---|---|
| Ingestion Contract Enforce | ALL incoming market data feeds MUST pass schema contract validation prior to pipeline ingestion. |
| DLQ Quarantine | Invalid records failing schema validation MUST be routed to a Dead Letter Queue (DLQ). |
| Null Ceiling | Batch null percentage MUST NOT exceed $0.5\%$ for critical price/volume fields. |
