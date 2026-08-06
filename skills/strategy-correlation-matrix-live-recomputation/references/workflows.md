# Workflows for Strategy Correlation Matrix Live Recomputation

1. **Return Series Collection**:
   - Collect live strategy return streams into DataFrame.
2. **EWMA & Shrinkage Estimation**:
   - Calculate EWMA covariance and apply Ledoit-Wolf shrinkage operator.
3. **Correlation Matrix Extraction**:
   - Standardize covariance into correlation matrix $R_{i,j}$.
4. **Breakdown Alerting**:
   - Issue warning alerts for strategy pairs exceeding $\rho \ge 0.70$.