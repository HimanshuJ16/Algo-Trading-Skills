# Standards for Vendor Latency Monitoring

| Metric | Engineering Standard |
|---|---|
| Percentile Calculation | P99 MUST be calculated directly from sample distributions (never averaged). |
| Timestamp Unit | All latency timestamps MUST be recorded in microseconds ($\mu\text{s}$). |
| SLA Limit | Vendor P99 latency exceeding SLA threshold MUST trigger an immediate alert. |
