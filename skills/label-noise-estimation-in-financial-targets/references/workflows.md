# Workflows for Label Noise Estimation

1. **Out-of-Fold Prediction Ingestion**:
   - Ingest observed noisy targets and cross-validated out-of-fold predicted class probabilities.
2. **Confident Learning Error Detection**:
   - Compute class-specific thresholds $t_k$ and identify mislabeled target samples.
3. **Noise Ratio & Weight Calculation**:
   - Estimate noise ratio $\eta$ and assign sample weights ($W_i = 0.0$ for noise).
4. **Audit Report Generation**:
   - Output structured label noise report.