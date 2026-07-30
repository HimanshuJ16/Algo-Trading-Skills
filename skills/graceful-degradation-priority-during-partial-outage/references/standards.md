# Standards for Graceful Degradation Priority

| Metric | Engineering Standard |
|---|---|
| P1 Task Protection | P1 (Risk/Cancel) tasks MUST NEVER be shed under any system mode. |
| Partial Degradation Threshold | P4 tasks MUST be shed if CPU $> 75\%$ or packet loss $> 1\%$. |
| Critical Outage Threshold | System MUST enter Capital Preservation Mode if packet loss $> 10\%$. |