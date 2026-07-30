# Workflows for Feature Selection Stability

1. **Fold Selection Ingestion**:
   - Ingest selected feature subsets across all K cross-validation folds.
2. **Inclusion Probability Computation**:
   - Calculate selection frequency $p_i$ for each candidate feature.
3. **Nogueira Index Calculation**:
   - Compute Nogueira stability measure adjusted for chance.
4. **Consensus Feature Filtering**:
   - Filter consensus features ($p_i \ge 0.80$) and discard unstable features.
