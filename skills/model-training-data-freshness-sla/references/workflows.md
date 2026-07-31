# Workflows for Training Data Freshness SLA

1. **Ingestion Lag Audit**:
   - Compute data lag hours based on latest market event timestamp.
2. **SLA Threshold Verification**:
   - Audit lag against target, warning, and breach SLA limits.
3. **Governance Action Execution**:
   - Trigger retraining halt or confidence scaling upon SLA breach.
4. **Audit Report Generation**:
   - Output structured freshness SLA report.
