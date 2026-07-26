# Workflows for Cross-Strategy Signal Reuse and Licensing

1. **Signal Registration**:
   - Record base fee, PnL share %, and max AUM capacity.
2. **Entitlement Audit**:
   - Check if $\sum \text{Subscribed AUM} + \text{New AUM} \le \text{Max Capacity}$.
3. **Transfer Pricing Calculation**:
   - $\text{Fee} = \text{Base Fee} + \text{PnL Share Pct} \times \max(0, \text{Strategy PnL})$.
4. **Audit Logging**:
   - Issue licensing certificate and fee schedule to consumer pod.