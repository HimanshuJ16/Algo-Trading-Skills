# Standards for Algo Wheel Execution Quality

| Metric | Target | Description |
|---|---|---|
| **Implementation Shortfall (IS)** | Lowest bps possible | The ultimate standard for TCA. Measures Slippage + Explicit Fees. |
| **Minimum Canary Flow** | 10% | Never set an underperforming broker to 0%. Always allocate a minimum amount of flow to continuously monitor if their algorithms have improved. |
| **Randomization** | Mandatory | To prevent brokers from gaming the system, the actual order routing should randomize the assignment based on the target probabilities. |

## Category
`execution-algorithms`
