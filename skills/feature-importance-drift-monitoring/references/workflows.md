# Workflows for Feature Importance Drift Monitoring

1. **Importance Profile Ingestion**:
   - Ingest baseline training feature importances and live production importances.
2. **Spearman Rank Correlation Computation**:
   - Compute rank ordering and calculate Spearman correlation coefficient ($\rho_{\text{rank}}$).
3. **Degradation & Shift Audit**:
   - Identify features experiencing severe drop in predictive contribution.
4. **Automated Retrain Dispatch**:
   - Dispatch model retrain signal if Spearman correlation drops below 0.70 threshold.
