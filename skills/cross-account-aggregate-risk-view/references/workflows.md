# Workflows for Cross-Account Aggregate Risk View

1. **Sub-Account Ingestion**:
   - Parse positions $\{s: Q_a(s)\}$ and balances $(\text{Cash}_a, \text{Margin}_a)$ for all accounts $a$.
2. **Consolidation**:
   - $Q_{net}(s) = \sum_a Q_a(s)$.
   - $\text{NAV}_{firm} = \sum_a \text{NAV}_a$.
   - $\text{GMV}_{firm} = \sum_s |Q_{net}(s) \cdot P(s)|$.
3. **Internal Offsetting Audit**:
   - Find symbols with concurrent long and short positions across sub-accounts.
4. **Pre-Trade Firm-Wide Check**:
   - Evaluate proposed order in account $A$.
   - If projected $\text{GMV}_{firm} > \text{Max GMV Limit} \implies$ Reject order.