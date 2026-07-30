# Workflows for Forex Currency Gain/Loss Tax Treatment

1. **Trade PnL Ingestion**:
   - Collect net realized PnL across spot forex and futures.
2. **Section 988 Calculation**:
   - $\text{Tax}_{988} = \text{PnL} \times \text{Ordinary Rate}$.
3. **Section 1256 Calculation**:
   - $\text{Blended Rate} = 0.60 \times \text{LTCG Rate} + 0.40 \times \text{STCG Rate}$.
   - $\text{Tax}_{1256} = \text{PnL} \times \text{Blended Rate}$.
4. **Election Audit**:
   - If Net PnL $> 0 \implies$ Recommend Section 1256 Opt-Out.
   - If Net PnL $< 0 \implies$ Recommend Section 988 Ordinary Loss.