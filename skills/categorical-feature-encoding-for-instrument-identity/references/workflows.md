# Workflows for Instrument Identity Encoding

1. **Data Preparation**: 
   - Ensure the dataset is strictly sorted by timestamp.
   - Define the `target` column (e.g., 1-day forward return).
2. **Expanding Window Encoding**:
   - Loop through unique timestamps (or use a grouped expanding apply in Pandas).
   - For a given timestamp $T$, filter all data where `timestamp < T`.
   - Calculate the `global_mean` of the target.
   - Group by `symbol` and calculate `count` and `local_mean`.
3. **Applying Smoothing Formula**:
   - $\text{Smoothed} = \frac{\text{count} \times \text{local\_mean} + \text{weight} \times \text{global\_mean}}{\text{count} + \text{weight}}$
4. **Integration**:
   - Drop the original `symbol` categorical string column before feeding the dataframe into the ML estimator (XGBoost/LightGBM/etc).
