# Standards for Data Quality Monitoring Dashboard

| Metric | Engineering Standard |
|---|---|
| Target Data Quality Score | Production market data feeds MUST maintain a composite DQ Score $\ge 85.0$. |
| Critical Failover Threshold | DQ Score $< 70.0$ or Dead Feed ($\text{TPS} = 0$) MUST trigger automatic fallback feed failover. |
| Ingestion Latency SLA | Ingestion latency MUST NOT exceed $100\text{ ms}$ for real-time tick feeds. |
