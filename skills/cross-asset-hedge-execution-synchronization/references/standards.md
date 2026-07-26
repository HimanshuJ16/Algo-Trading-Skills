# Standards for Cross-Asset Hedge Execution Synchronization

| Metric | Engineering Standard |
|---|---|
| Max Synchronization SLA | Hedge leg orders MUST be dispatched within 100 ms of primary fill notification. |
| Partial Fill Hedging | Partial fills MUST be hedged incrementally without waiting for full order completion. |
| Emergency Unwind Threshold | Un-hedged legs exceeding 500 ms MUST trigger emergency primary position unwinding. |