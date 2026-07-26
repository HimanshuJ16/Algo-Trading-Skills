# Pre-Flight Checklist

- [ ] Are heartbeat probes active on all cross-region database and Kafka topic links?
- [ ] Are P95 and P99 percentiles calculated across rolling heartbeat windows?
- [ ] Is secondary replica status marked `UNSAFE_STALE` when P99 lag exceeds $500\text{ ms}$?
- [ ] Is automatic read-failover to primary database configured for stale replicas?
