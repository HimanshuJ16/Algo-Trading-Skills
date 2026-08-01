# Standards for Reference Data Change Notification Pipeline

| Metric | Engineering Standard |
|---|---|
| Critical Fields | Changes to `symbol`, `exchange`, `status`, `currency` MUST trigger CRITICAL alerts. |
| Detection Latency | Changes MUST be detected within $5\text{ minutes}$ of snapshot update. |
| Downstream Notification | All registered consumers MUST be notified within $1\text{ minute}$ of detection. |
