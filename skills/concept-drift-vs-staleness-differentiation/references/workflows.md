# Workflows for Drift vs. Staleness Classification

1. **Telemetry Data Input**:
   - Reference feature dataset $X_{ref}$, Current feature dataset $X_{curr}$.
   - Reference target error $e_{ref} = \hat{Y}_{ref} - Y_{ref}$, Current error $e_{curr} = \hat{Y}_{curr} - Y_{curr}$.
   - Feature update timestamp $T_{feat}$, System timestamp $T_{sys}$.
2. **Feature Shift Calculation**:
   - Compute average Population Stability Index across features:
     $$\text{PSI} = \sum (P_i - Q_i) \times \ln\left(\frac{P_i}{Q_i}\right)$$
3. **Error Ratio Calculation**:
   - $\text{MSE Ratio} = \frac{\text{Mean}(e_{curr}^2)}{\text{Mean}(e_{ref}^2)}$.
4. **Classification Decision Tree**:
   - If $(T_{sys} - T_{feat}) > \text{Max\_Staleness\_Sec} \implies$ `DATA_STALENESS`.
   - Else if $\text{PSI} > 0.25$ and $\text{MSE Ratio} < 1.3 \implies$ `COVARIATE_SHIFT`.
   - Else if $\text{MSE Ratio} \ge 1.5 \implies$ `CONCEPT_DRIFT`.
   - Else $\implies$ `STABLE`.