# Standards for Cross-Region Data Replication Lag Monitoring

| Metric | Engineering Standard |
|---|---|
| Max P99 Replication SLA | Cross-region database replication P99 lag MUST NOT exceed $500\text{ ms}$. |
| Stale Read Isolation | Secondary replicas exceeding $500\text{ ms}$ P99 lag MUST be isolated from execution read paths. |
| Continuous Heartbeat Probe | Heartbeat write probes MUST be issued at least once per second per region pair. |
