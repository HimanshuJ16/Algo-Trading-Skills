# Standards for Smart Order Router Failover on Venue Outage

| Parameter | Standard Rule |
|---|---|
| Error Threshold | $\ge 3$ consecutive timeouts/errors MUST trip circuit breaker. |
| Failover Latency SLA | Automatic failover to secondary venue MUST occur within $< 10$ milliseconds. |
| State Recovery | Outage venue MUST undergo 60-second cooldown before probe recovery. |
