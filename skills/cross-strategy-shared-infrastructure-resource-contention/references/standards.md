# Standards for Shared Infrastructure Resource Contention

| Metric | Engineering Standard |
|---|---|
| Critical Utilization Threshold | Resource utilization $\ge 85\%$ MUST trigger automated low-priority task preemption. |
| CPU Core Pinning | `HIGH_HFT` strategies MUST be pinned to dedicated physical CPU cores with zero OS thread migration. |
| FIX Rate Limit Protection | Shared FIX gateway messages MUST NOT exceed $90\%$ of broker-negotiated msg/sec rate limits. |