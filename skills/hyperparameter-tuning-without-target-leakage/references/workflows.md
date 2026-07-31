# Workflows for Leakage-Free Hyperparameter Tuning

1. **Outer/Inner Nested Split Setup**:
   - Define Outer chronologically split folds and Inner tuning folds.
2. **Purging & Embargoing Buffer Application**:
   - Apply Purging to overlapping label horizons and Embargoing to post-validation gaps.
3. **Isolated Preprocessing & Grid Search**:
   - Fit feature scalers strictly on Inner Train folds; evaluate hyperparameter grid.
4. **Out-of-Sample Performance Evaluation**:
   - Evaluate best hyperparameters on held-out Outer Test fold and generate report.
