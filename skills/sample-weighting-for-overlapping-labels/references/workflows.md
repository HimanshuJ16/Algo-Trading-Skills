# Workflows for Sample Weighting for Overlapping Labels

1. **Concurrency Matrix Computation**:
   - Calculate active concurrent label count $c_t$ at each bar $t$.
2. **Uniqueness Calculation**:
   - Compute mean inverse concurrency $u_i = \text{mean}(1 / c_t)$ across span duration.
3. **Weight Assignment & Normalization**:
   - Compute uniqueness, return-attributed, or time-decayed weights; normalize to sum to N.
4. **Model Training Integration**:
   - Pass sample weights into ML model fit routine (`sample_weight` in scikit-learn / XGBoost).
