# Standards for Capacity Planning

| Metric | Engineering Standard |
|---|---|
| Safety Margin | Always multiply peak calculated load by **1.5x to 2.0x** for microburst headroom. |
| Network Utilization | Never design a system to run at >60% of max NIC capacity (to avoid TCP buffer bloat). |
| Latency vs Throughput | Partition by symbol (e.g., A-M on Core 1, N-Z on Core 2) rather than using lock-based shared state. |