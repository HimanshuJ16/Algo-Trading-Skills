# Standards for Risk Parity Allocation Across Strategies

| Metric | Engineering Standard |
|---|---|
| Target Risk Contribution | Each strategy MUST contribute an equal percentage ($\frac{100\%}{N}$) to portfolio volatility. |
| Maximum Risk Error | Maximum risk contribution error MUST NOT exceed $\le 5.0\%$. |
| Rebalancing Trigger | Rebalancing MUST trigger when strategy volatility drifts by $> 20\%$ relative to target. |