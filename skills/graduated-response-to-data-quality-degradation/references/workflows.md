# Workflows for Graduated Response to Data Quality Degradation

1. **Metric Ingestion**:
   - Ingest stale time, sequence gaps, price spikes, and book state.
2. **Quality Score Calculation**:
   - Compute data quality score $Q \in [0, 100\%]$.
3. **Tier Classification & Action Mapping**:
   - Map $Q$ to De-Risking Tiers 0-3 (Full Trading, 50% Size, Block Entries, Emergency Flatten).
4. **Audit Logging**:
   - Output structured de-risk report.