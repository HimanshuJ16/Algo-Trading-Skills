# Standards for Custody Solution Uptime and Liveness Guarantees

| Metric | Engineering Standard |
|---|---|
| Target Uptime SLA | Primary custody provider API MUST maintain $\ge 99.9\%$ rolling monthly availability. |
| MPC Quorum Rule | Active signing nodes MUST NOT fall below $k$ threshold (e.g. 2-of-3 MPC). |
| P99 Signing Latency SLA | P99 transaction signing latency MUST NOT exceed $2000\text{ ms}$. |