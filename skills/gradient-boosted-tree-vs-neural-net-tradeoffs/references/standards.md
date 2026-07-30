# Standards for Model Family Selection

| Metric | Engineering Standard |
|---|---|
| Tabular Data Preference | Tabular financial datasets MUST default to GBDT (LightGBM/XGBoost) baseline. |
| SR 11-7 Compliance | Regulated signals MUST require SHAP / EBM feature explainability. |
| Sub-Millisecond Latency | Sub-500 microsecond strategies MUST prefer GBDT C++ runtimes over PyTorch/GPU. |
