# Workflows for Strategy Performance Attribution vs Market Beta

1. **Factor Data Ingestion**:
   - Collect strategy daily returns, market returns, SMB, and HML factor data.
2. **Excess Return Calculation**:
   - Subtract risk-free rate from strategy and factor returns.
3. **OLS Regression**:
   - Fit multi-factor OLS model to estimate Alpha ($\alpha$), Betas ($\beta_M, \beta_S, \beta_H$), $t$-statistics, and $R^2$.
4. **Attribution Reporting**:
   - Output decomposed return contributions and audit true alpha significance.