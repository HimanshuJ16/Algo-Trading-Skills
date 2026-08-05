# Standards for Risk Limit Breach Escalation Matrix

| Metric | Engineering Standard |
|---|---|
| Threshold Multipliers | WARN (1.0x), REDUCE (1.2x), HALT (1.5x), FLATTEN (2.0x). |
| Duration Escalation | Sustained breaches $> 300\text{s}$ MUST auto-escalate to the next severity tier. |
| Acknowledgment SLA | CRITICAL breaches MUST require PagerDuty acknowledgment within $< 60\text{s}$. |
