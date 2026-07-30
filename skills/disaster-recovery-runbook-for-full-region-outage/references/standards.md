# Standards for Disaster Recovery Runbook for Full Region Outage

| Metric | Engineering Standard |
|---|---|
| Target RTO Limit | Automated regional DR failover MUST complete in $\le 300\text{ seconds}$ (5 minutes). |
| Target RPO Limit | Cross-region database replication lag MUST NOT exceed $\le 15\text{ seconds}$. |
| Pre-Failover Kill Switch | Cancel-all-orders signal MUST be dispatched prior to DNS traffic rerouting. |
