# Workflows for Cloud Cost Anomaly Detection

1. **Ingestion**:
   - Collect daily spend per service: $C_1, C_2, \dots, C_k$.
2. **Baseline Computation**:
   - Compute mean $\mu = \frac{1}{N}\sum C_i$, std dev $\sigma = \sqrt{\frac{1}{N}\sum (C_i - \mu)^2}$.
3. **Z-Score Audit**:
   - Calculate $Z = \frac{C_{\text{curr}} - \mu}{\sigma + \epsilon}$.
4. **Classification & Alerting**:
   - If $Z \ge 3.0 \implies$ `CRITICAL` Cost Anomaly.
   - If $Z \ge 2.0 \implies$ `WARNING`.
   - Else $\implies$ `NORMAL`.
5. **Unit Cost Analysis**:
   - $\text{Unit Cost} = C_{\text{curr}} / \text{Trading\_Volume}$.