# Workflows for Risk Parity Allocation Across Strategies

1. **Volatility & Covariance Estimation**:
   - Estimate annualized volatility and optional covariance matrix across candidate strategies.
2. **Inverse-Volatility Weighting**:
   - Compute normalized inverse-volatility capital weights ($w_i \propto 1/\sigma_i$).
3. **Risk Contribution Decomposition**:
   - Calculate portfolio volatility and marginal contribution to risk (MCR).
4. **Risk Balance Verification & Allocation Report**:
   - Verify risk contribution equality within $\le 5\%$ error margin and output report.