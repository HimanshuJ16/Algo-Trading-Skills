# Workflows for Data Quality Monitoring Dashboard

1. **Batch Telemetry Collection**:
   - Collect record counts, nulls, duplicates, outliers, latency, and TPS.
2. **Pillar Scoring**:
   - Score Completeness, Timeliness, Accuracy, Uniqueness, and Liveness.
3. **Composite Index Calculation**:
   - $\text{DQ Score} = 0.25 S_{\text{comp}} + 0.25 S_{\text{time}} + 0.25 S_{\text{acc}} + 0.15 S_{\text{uniq}} + 0.10 S_{\text{live}}$.
4. **Alerting & Failover**:
   - Trigger secondary feed failover if $\text{DQ Score} < 70.0$.
