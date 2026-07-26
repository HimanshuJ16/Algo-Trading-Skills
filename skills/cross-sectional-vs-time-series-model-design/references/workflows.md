# Workflows for Cross-Sectional vs Time-Series Model Design

1. **Cross-Sectional Workflow**:
   - For timestamp $t$, collect raw factor values $X_{1,t}, X_{2,t}, \dots, X_{K,t}$.
   - Compute mean $\mu_{cs,t}$ and std dev $\sigma_{cs,t}$ across assets.
   - $Z_{i,t} = (X_{i,t} - \mu_{cs,t}) / \sigma_{cs,t}$.
   - Demean weights: $w_{i,t} = \frac{Z_{i,t} - \bar{Z}_t}{\sum |Z_{i,t} - \bar{Z}_t|}$.
2. **Time-Series Workflow**:
   - For asset $i$, collect historical factor values over window $W$: $X_{i,t-W:t}$.
   - Compute mean $\mu_{ts,i}$ and std dev $\sigma_{ts,i}$ over time.
   - $Z_{i,t} = (X_{i,t} - \mu_{ts,i}) / \sigma_{ts,i}$.
   - Scale weights: $w_{i,t} = \text{sign}(Z_{i,t}) \times \frac{\sigma_{target}}{\sigma_{realized, i}}$.
