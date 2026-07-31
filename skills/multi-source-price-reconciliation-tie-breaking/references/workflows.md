# Workflows for Multi-Source Price Reconciliation Tie Breaking

1. **Outlier Filtering**:
   - Compute median price and filter quotes deviating beyond max deviation threshold.
2. **Tolerance Audit**:
   - Compute reliability-weighted canonical price if valid quotes agree within tolerance.
3. **Deterministic Tie-Breaking**:
   - Apply PRIORITY, FRESHNESS, or VOLUME_WEIGHTED rules if vendor quotes conflict.
4. **Audit Report Generation**:
   - Output structured price reconciliation report.
