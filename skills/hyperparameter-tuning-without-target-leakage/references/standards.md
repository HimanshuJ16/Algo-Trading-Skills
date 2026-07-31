# Standards for Leakage-Free Hyperparameter Tuning

| Metric | Engineering Standard |
|---|---|
| Tuning Architecture | Hyperparameter tuning MUST execute inside Inner Nested Cross-Validation folds. |
| Purging & Embargoing | Overlapping label horizons MUST be purged; $1.0\%$ embargo buffer MUST follow validation folds. |
| Feature Scaler Isolation | Feature scalers MUST be fit strictly on inner training fold data. |
