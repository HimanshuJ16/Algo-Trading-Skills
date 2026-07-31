# Workflows for Multi-Horizon Forecasting

1. **Prediction Ingestion**:
   - Ingest multi-horizon return predictions ($\tau_1, \dots, \tau_K$).
2. **Horizon Weighting**:
   - Calculate normalized weights based on IC or inverse square root horizon decay.
3. **Alpha Signal & Conflict Audit**:
   - Synthesize composite alpha signal and evaluate directional consensus/conflict.
4. **Audit Report Generation**:
   - Output structured multi-horizon forecast report.
