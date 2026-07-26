# Workflows for Cross-Asset Correlation Regime Shifts

1. **Matrix Estimation**:
   - Estimate short-term matrix $C_{short}$ (20 days) and baseline matrix $C_{long}$ (100 days).
2. **Frobenius Distance Calculation**:
   - $D_F = \sqrt{\sum_{i,j} (C_{short, i,j} - C_{long, i,j})^2}$.
3. **Average Correlation**:
   - $\bar{\rho}_{short} = \frac{1}{K(K-1)} \sum_{i \neq j} C_{short, i,j}$.
4. **Regime Classification**:
   - If $D_F > 0.80$ or $\bar{\rho}_{short} > 0.65 \implies$ `CRISIS_CONVERGENCE`.
   - If $0.40 < D_F \le 0.80 \implies$ `CORRELATION_SHIFT`.
   - Else $\implies$ `STABLE_NORMAL`.
5. **Portfolio Action**:
   - Reduce risk-parity portfolio leverage when `CRISIS_CONVERGENCE` is active.
