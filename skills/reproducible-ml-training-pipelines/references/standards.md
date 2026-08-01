# Standards for Reproducible ML Training Pipelines

| Metric | Engineering Standard |
|---|---|
| Hashing Algorithm | SHA-256 MUST be used for dataset, hyperparameter, and weights hashing. |
| Seed Binding | Global seed MUST be logged in every experiment manifest. |
| Code Versioning | Git commit hash MUST be embedded in the model reproducibility manifest. |