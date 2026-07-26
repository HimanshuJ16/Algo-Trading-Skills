# Workflows for Cross-Margining Across Asset Classes

1. **Margin Component Ingestion**:
   - Collect standalone initial margin requirements $M_1, M_2, \dots, M_K$.
2. **Correlation Matrix Application**:
   - Apply clearing house correlation offset matrix $\rho_{i,j}$.
3. **Cross-Margin Computation**:
   - $M_{\text{cross}} = \max\left(0.20 \times M_{\text{standalone}}, \sqrt{\sum M_i^2 + 2\sum_{i<j} \rho_{i,j} M_i M_j}\right)$.
4. **Savings & Efficiency Reporting**:
   - $\text{Savings} = M_{\text{standalone}} - M_{\text{cross}}$.
   - $\text{Efficiency Pct} = \frac{\text{Savings}}{M_{\text{standalone}}} \times 100\%$.