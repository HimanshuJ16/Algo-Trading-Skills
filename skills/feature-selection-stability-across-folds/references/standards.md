# Standards for Feature Selection Stability

| Metric | Engineering Standard |
|---|---|
| Nogueira Stability Index Threshold | Feature set MUST achieve Nogueira Index $\Phi \ge 0.70$. |
| Consensus Inclusion Threshold | Selected features MUST be present in $\ge 80\%$ of CV folds ($p_i \ge 0.80$). |
| Minimum Cross-Validation Folds | Stability analysis MUST use at least $K \ge 5$ CV folds. |
