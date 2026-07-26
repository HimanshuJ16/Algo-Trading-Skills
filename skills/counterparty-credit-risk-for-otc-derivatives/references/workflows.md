# Workflows for OTC Counterparty Credit Risk

1. **Netting Set Aggregation**:
   - Sum MTM values for all contracts in netting set: $V_{net} = \sum V_{mtm, i}$.
2. **Current Exposure Calculation**:
   - $CE_{net} = \max(0, V_{net} - \text{Collateral} - \text{Threshold})$.
3. **PFE & EAD Calculation**:
   - $\text{PFE} = \sum (\text{Notional}_i \times \text{RiskFactor}_i)$.
   - $EAD = CE_{net} + \text{PFE}$.
4. **CVA Pricing Adjustment**:
   - $CVA = (1 - R) \times EAD \times PD$.
5. **CSA Margin Call Audit**:
   - Uncollateralized Amount $= V_{net} - \text{Collateral}$.
   - If Uncollateralized Amount $> \text{Threshold} + \text{MTA} \implies$ Trigger Margin Call.
