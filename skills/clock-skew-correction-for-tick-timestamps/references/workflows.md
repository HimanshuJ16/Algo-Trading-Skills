# Workflows for Clock Skew Correction

1. **Telemetry Data Input**:
   - Input dataframe/arrays containing `exchange_ts` (seconds/nanoseconds) and `local_ts`.
2. **Min-Delay Binning**:
   - Segment data into non-overlapping time windows (e.g. 10-second intervals).
   - For each window, calculate raw delay $D_i = T_{local} - T_{exchange}$.
   - Identify the minimum delay $D_{min, k}$ in window $k$.
3. **Linear Model Estimation**:
   - Fit $D_{min, k} = \alpha + \beta \cdot T_{exchange, k}$.
   - $\alpha$ is the baseline offset, $\beta$ is the clock drift rate (skew).
4. **Correction and Enforcement**:
   - For each tick $i$, calculated skew-corrected local time: $T_{corr, i} = T_{local, i} - (\alpha + \beta \cdot T_{exchange, i})$.
   - Pass through a monotonicity filter: $T_{final, i} = \max(T_{corr, i}, T_{final, i-1} + \epsilon)$.
