# Workflows for Cross-Chain Bridge Risk Management

1. **De-Peg Monitoring**:
   - Compute $\text{Depeg Pct} = \left|\frac{P_{\text{wrapped}} - P_{\text{native}}}{P_{\text{native}}}\right| \times 100\%$.
2. **Cap & Finality Audit**:
   - Evaluate proposed transfer amount $V$ against max in-flight NAV cap ($15\%$).
   - Verify finality latency $\le \text{Max SLA Delay}$.
3. **Routing & Execution**:
   - Approve transfer if all safety parameters pass; otherwise failover to secondary bridge.
4. **Emergency Pause**:
   - Trigger automated pause on bridge routing if de-peg exceeds $1.0\%$.
