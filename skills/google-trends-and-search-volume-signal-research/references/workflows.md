# Workflows for Google Trends Signal Research

1. **SVI Data Ingestion & Lag Shift**:
   - Ingest raw SVI scores and shift by 24h publication lag.
2. **Rolling Z-Score Computation**:
   - Compute rolling mean and std dev to calculate Z-score.
3. **Signal Classification**:
   - Classify attention surges vs panic spikes using price momentum.
4. **Signal Audit Generation**:
   - Output structured signal report.