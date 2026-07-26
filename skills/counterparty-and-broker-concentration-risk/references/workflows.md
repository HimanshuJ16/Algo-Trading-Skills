# Workflows for Counterparty Concentration Risk

1. **Exposure Accounting**:
   - For each broker $k$, sum $\text{Cash}_k + \text{Margin}_k + \text{Positions}_k$.
2. **Pre-Trade Routing Evaluation**:
   - Input proposed order value $V$ for target broker $K_1$.
   - Calculate projected weight: $w_{\text{proj}} = (\text{Exposure}_{K_1} + V) / \text{NAV}$.
   - If $w_{\text{proj}} > \text{Max\_Limit}_{K_1}$ OR $\text{CDS}_{K_1} > 250\text{ bps}$:
     - Search secondary brokers $K_2, K_3$ for lowest compliant weight.
3. **Failover Execution Dispatch**:
   - Re-route order $V$ to selected secondary broker.
4. **Broker HHI Metric**:
   - Compute $HHI = \sum w_k^2$. Raise alert if $HHI > 0.35$.
