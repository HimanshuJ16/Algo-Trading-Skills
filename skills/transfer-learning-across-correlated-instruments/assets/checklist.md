# Institutional Financial ML Transfer Learning Checklist

## Source Asset Selection & Feature Alignment
- [ ] **Correlation Screening**: Verify source asset exhibits $r \ge 0.60$ target correlation with the cold-start target asset.
- [ ] **Feature Parity**: Ensure identical feature definitions, lag structures, and sampling frequencies between Source and Target datasets.
- [ ] **Covariate Shift Evaluation**: Execute `calculate_covariate_shift()` to confirm domain distance is $\le 2.0$.

## Model Pre-Training & Fine-Tuning
- [ ] **Source Model Pre-Training**: Fit base regression/neural model on liquid Source dataset; save feature normalization parameters (`feature_means`, `feature_stds`).
- [ ] **Feature Scaler Alignment**: Transform Target features using **Source feature scalers** to maintain feature space alignment.
- [ ] **L2 Regularization Parameter ($\lambda$)**: Calibrate fine-tuning L2 penalty parameter ($\lambda \ge 0.1$) to prevent overfitting on sparse target data.

## Validation & Negative Transfer Audits
- [ ] **Direct Target Baseline**: Train a direct cold-start baseline model strictly on Target data for OOS benchmark comparison.
- [ ] **Out-Of-Sample (OOS) $R^2$ Comparison**: Confirm Transferred Model achieves $R^2_{\text{transfer}} > R^2_{\text{direct\_target}}$.
- [ ] **Audit Log Archival**: Archive transfer evaluation metrics, correlation scores, domain shift scores, and weight adaptation logs.