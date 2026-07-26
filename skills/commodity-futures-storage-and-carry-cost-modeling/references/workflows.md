# Workflows for Commodity Carry Cost Modeling

1. **Input Data Loading**:
   - Spot Price $S_0$, Futures Market Price $F_{market}$, Time to Expiration $T$ (years), Annual Risk-Free Rate $r$, Storage Cost Rate $c$.
2. **Implied Convenience Yield Calculation**:
   - $y = r + c - \frac{1}{T} \ln\left(\frac{F_{market}}{S_0}\right)$.
3. **Term Structure & Curve Slope Analysis**:
   - Calculate Basis: $\text{Basis} = S_0 - F_{market}$.
   - Determine Market State: `CONTANGO` ($F > S$) vs `BACKWARDATION` ($F < S$).
4. **Arbitrage Threshold Audit**:
   - $\text{Fair Futures Price } F_{fair} = S_0 \cdot e^{(r + c - y_{baseline}) T}$.
   - If $F_{market} > F_{fair} + \text{Traded Costs}$, generate Cash-and-Carry signal.