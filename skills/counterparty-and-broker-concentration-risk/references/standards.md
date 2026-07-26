# Standards for Counterparty Concentration Risk

| Metric | Engineering Standard |
|---|---|
| Single Broker NAV Cap | No single prime broker MUST hold $> 35\%$ of total portfolio NAV in combined cash/margin/positions. |
| CDS Distress Threshold | A broker CDS spread $> 250\text{ bps}$ MUST immediately pause routing new long/cash allocations to that broker. |
| Multi-Broker Readiness | At least 2 active prime broker connections MUST be maintained live for instant failover routing. |
