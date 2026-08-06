# Standards for Strategy-Level vs Portfolio-Level Kill Switch

| Circuit Breaker Tier | Typical Standard Limit | Action |
|---|---|---|
| Strategy-Level Kill Switch | $10.0\%$ Strategy Drawdown | Halt/Liquidate SINGLE strategy. |
| Portfolio-Level Kill Switch | $15.0\%$ Fund Drawdown | Halt/Liquidate ALL fund strategies. |
| Cascade Threshold | $\ge 3$ Tripped Strategies | Triggers Master Portfolio Kill Switch. |