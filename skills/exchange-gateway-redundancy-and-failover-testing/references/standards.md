# Standards for Exchange Gateway Redundancy and Failover Testing

| Metric | Engineering Standard |
|---|---|
| Failover Recovery Time (RTO) | Gateway failover execution MUST complete within $< 100\text{ms}$. |
| Sequence Number Sync | Secondary gateway MUST synchronize MsgSeqNum before sending logon. |
| In-Flight Duplicate Guard | Retransmitted in-flight orders MUST set `PossDupFlag = Y` (Tag 43). |
