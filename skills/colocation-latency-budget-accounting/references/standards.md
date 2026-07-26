# Standards for Latency Budget Accounting

| Metric | Engineering Standard |
|---|---|
| Hardware Timestamping | Ingress ($T_0$) and Egress ($T_5$) timestamps MUST originate from hardware NIC packet descriptors. |
| Zero Hot-Path Allocation | Timestamp recording in the hot path MUST NOT allocate heap memory or execute blocking calls. |
| Tail Metric Primacy | Performance SLAs MUST be evaluated against $P_{99}$ and $P_{99.9}$ percentiles, never simple arithmetic mean. |
