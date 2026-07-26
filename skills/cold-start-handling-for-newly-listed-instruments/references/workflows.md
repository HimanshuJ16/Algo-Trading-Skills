# Workflows for Cold Start Handling

1. **Instrument Onboarding**:
   - Query instrument metadata: `list_date`.
   - Calculate $N_{obs} = \text{Current Date} - \text{List Date}$ in trading days.
2. **Feature Imputation Pipeline**:
   - If $N_{obs} == 0$: Return 100% Peer Prior values for volatility and feature encodings.
   - If $0 < N_{obs} < N_{min\_warmup}$:
     - Calculate linear weight $w = N_{obs} / N_{min\_warmup}$.
     - Impute $\sigma = w \cdot \sigma_{obs} + (1 - w) \cdot \sigma_{peer}$.
3. **Risk Management & Position Sizing**:
   - $\text{Max Capital} = \text{Target Allocation} \times w$.
4. **Graduation Event**:
   - At $N_{obs} = N_{min\_warmup}$, mark status as `GRADUATED` and remove shrinkage constraints.
