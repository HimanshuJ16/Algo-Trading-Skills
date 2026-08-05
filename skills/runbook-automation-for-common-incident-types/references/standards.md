# Standards for Runbook Automation for Common Incident Types

| Incident Type | Automated Remediation Playbook | Max Execution SLA |
|---|---|---|
| FEED_DISCONNECT | Reconnect Socket $\to$ Failover Venue | $< 500\text{ms}$ |
| LATENCY_SPIKE | Throttle Order Rate $\to$ Failover Venue | $< 100\text{ms}$ |
| BROKER_API_OUTAGE | Cancel Open Orders $\to$ Failover Venue | $< 1000\text{ms}$ |
| DRAWDOWN_BREACH | Cancel Open Orders $\to$ Trigger Kill Switch | $< 50\text{ms}$ |