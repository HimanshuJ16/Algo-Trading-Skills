# Workflows for Model Versioning & Rollback

1. **Model Artifact SHA-256 Registration**:
   - Compute SHA-256 hash and store model metadata in Model Registry.
2. **Live Telemetry & Breaker Audit**:
   - Monitor live drawdown and inference error rates against thresholds.
3. **Automated Rollback Hot-Swap**:
   - Hot-swap active pointer to last healthy production version upon breach.
4. **Audit Report Generation**:
   - Output structured model version report.
