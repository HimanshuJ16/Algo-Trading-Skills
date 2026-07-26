# Workflows for Cross-Strategy Correlation Monitoring

1. **PnL Ingestion**:
   - Collect PnL return vectors $R_1, R_2, \dots, R_M$.
2. **Correlation Calculation**:
   - Compute Pearson correlation matrix $C_{\text{pnl}}$ over rolling window.
3. **Breach Audit**:
   - Filter pairs where $\rho_{i,j} \ge 0.70$.
4. **Diversification Ratio Computation**:
   - $DR = \frac{\sum w_i \sigma_i}{\sqrt{w^T \Sigma w}}$.
5. **Re-allocation Action**:
   - Trigger capital reduction for redundant strategy pairs.