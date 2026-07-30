# Pre-Flight Checklist

- [ ] Are baseline training feature importances recorded in model registry?
- [ ] Is live production feature importance computed periodically (SHAP / Permutation)?
- [ ] Is Spearman rank correlation ($\rho_{\text{rank}}$) calculated across feature ranks?
- [ ] Is automated retrain alert triggered when $\rho_{\text{rank}} < 0.70$?
