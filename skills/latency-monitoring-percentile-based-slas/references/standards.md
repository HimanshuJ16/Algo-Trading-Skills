# Standards for Latency Percentile SLAs

| Metric | Engineering Standard |
|---|---|
| SLA Metric Base | SLAs MUST be evaluated on P99 and P99.9 percentiles, NEVER on arithmetic averages. |
| P99 SLA Limit | 99% of order executions MUST complete within $\le 200\ \mu\text{s}$. |
| P99.9 Tail Limit | 99.9% of order executions MUST complete within $\le 1,000\ \mu\text{s}$ ($1\text{ ms}$). |
