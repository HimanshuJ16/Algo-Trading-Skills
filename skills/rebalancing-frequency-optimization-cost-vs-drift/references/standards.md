# Standards for Rebalancing Frequency Optimization Cost vs Drift

| Metric | Engineering Standard |
|---|---|
| Max Drift Threshold | Single asset weight drift MUST NOT exceed $5.0\%$ without triggering rebalance. |
| Net Benefit Rebalance Condition | Rebalance MUST occur if $\text{NetBenefit} = \text{DriftCost} - \text{TxCost} > 0$. |
| No-Trade Band Tolerance | Asset weights within $\pm 2.0\%$ of target SHOULD NOT be traded. |