# Workflows for Credit Default Swap Basics

1. **Hazard Rate Estimation**:
   - $\lambda = \frac{s_{par}}{1 - R}$.
2. **Survival & Default Probabilities**:
   - $S(T) = e^{-\lambda T}$.
   - $PD(T) = 1 - S(T)$.
3. **Risky PV01 (RPV01)**:
   - $RPV01 = \frac{1 - e^{-(r + \lambda) T}}{r + \lambda}$.
4. **ISDA Upfront Payment**:
   - $\text{Upfront} = \text{Notional} \times RPV01 \times (s_{par} - s_{coupon})$.
5. **Cross-Asset Signal Execution**:
   - If $\Delta s_{par} > \text{Threshold} \implies$ Trigger Equity Short / CDS Long signal.