# Standards for Financial Label Noise Estimation

| Metric | Engineering Standard |
|---|---|
| Prediction Source | Out-of-fold cross-validated probabilities MUST be used for noise detection. |
| Threshold Calculation | Thresholds $t_k$ MUST be calculated independently for each target class. |
| High Noise Warning | Noise ratio $\eta \ge 20\%$ MUST trigger a high noise warning. |