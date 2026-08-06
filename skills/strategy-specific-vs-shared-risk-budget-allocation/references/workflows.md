# Workflows for Strategy-Specific vs Shared Risk Budget Allocation

1. **Covariance Matrix Ingestion**:
   - Ingest strategy return covariance matrix $\Sigma$ and target capital weights $w$.
2. **Euler MCR & Component Risk Calculation**:
   - Compute Marginal Contribution to Risk (MCR) and Component Risk fractions.
3. **Euler Identity Verification**:
   - Verify that component risk contributions sum to $100\%$.
4. **Dual-Tier Audit & Capital Re-scaling**:
   - Audit standalone volatility limits and shared risk budgets; output capital adjustment factors.