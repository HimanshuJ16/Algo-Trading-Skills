# Workflows for Explainable Boosting Machines (EBM)

1. **Shape Function Ingestion**:
   - Ingest 1D shape tables $f_i(x_i)$ and 2D interaction tables $f_{jk}(x_j, x_k)$.
2. **Exact Feature Evaluation**:
   - Evaluate exact contribution per input feature and interaction pair.
3. **Additive Score Composition**:
   - Sum base intercept $\beta_0$ and all shape evaluations to derive final signal score.
4. **SR 11-7 Governance Logging**:
   - Log exact additive contributions for model risk compliance.