# Pre-Flight Checklist

- [ ] Are DQ scores calculated across Completeness, Timeliness, Accuracy, Uniqueness, and Liveness?
- [ ] Do the configured pillar weights sum to 1.0, and is `critical_failover_score` strictly below `min_healthy_score`?
- [ ] Are the score thresholds, penalty factors, and `latency_zero_score_ms` tuned to this vendor and strategy rather than left at library defaults?
- [ ] Are stalled feeds ($\text{TPS} = 0$) escalated to `CRITICAL` even when the composite score is above the healthy floor?
- [ ] Is liveness corroborated by a source-side timestamp check, not by tick rate alone (a feed replaying stale data still reports $\text{TPS} > 0$)?
- [ ] Does the caller catch `ValueError` from `audit_feed_quality` and alert on it, rather than treating a corrupt telemetry batch as "no result"?
- [ ] Is the measurement window short enough that an open-auction latency spike is not averaged away?
- [ ] Are outlier counts corroborated across vendors before failover, so a real news-driven move is not treated as a data defect?
- [ ] Is secondary feed failover triggered when the composite drops below `critical_failover_score`?
- [ ] Are DQ alerts routed to the operational alerting path (e.g. Grafana/PagerDuty), and is alert dispatch latency itself monitored?
