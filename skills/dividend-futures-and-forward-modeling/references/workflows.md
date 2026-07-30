# Workflows for Dividend Futures and Forward Modeling

1. **Dividend Schedule Ingestion**:
   - Collect expected discrete dividend amounts $D_i$ and payment times $t_i$.
2. **Present & Future Value Computation**:
   - $\text{PV}(D) = \sum D_i e^{-r t_i}$, $\text{FV}(D) = \sum D_i e^{r(T - t_i)}$.
3. **Theoretical Forward Pricing**:
   - $F_{\text{theoretical}}(0, T) = (S_0 - \text{PV}(D)) e^{r T}$.
4. **Arbitrage Opportunity Auditing**:
   - Compare market forward price against theoretical bounds to detect mis-pricing.