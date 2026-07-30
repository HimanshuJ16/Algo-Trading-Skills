# Standards for Feature Importance Drift Monitoring

| Metric | Engineering Standard |
|---|---|
| Spearman Rank Threshold | Model retrain MUST be triggered if Spearman correlation $\rho_{\text{rank}} < 0.70$. |
| Feature Importance Ranking | Ranks MUST be evaluated using non-parametric Spearman rank correlation. |
| Max Feature Degradation | Any top-3 baseline feature dropping $> 80\%$ in live importance MUST trigger an audit. |
