# Workflows for Patent Filing Data for Innovation Signal Research

1. **Feature Extraction**:
   - Calculate patent velocity and logarithmic forward citation impact per asset.
2. **IQS Score Calculation**:
   - Compute weighted Innovation Quality Score (IQS = velocity_weight * V + citation_weight * C).
3. **Z-Score Normalization**:
   - Normalize IQS scores across cross-sectional universe into Z-scores.
4. **Audit Report Generation**:
   - Output structured patent innovation report.