# Workflows for Model A/B Testing

1. **Traffic Routing & Execution Audit**:
   - Hash incoming requests to route to Champion or Challenger models.
2. **Sample Collection**:
   - Collect trade return samples in basis points for both models.
3. **Welch's t-Test & Significance Audit**:
   - Compute Welch's t-statistic, degrees of freedom, and p-value.
4. **Audit Report Generation**:
   - Output structured A/B test report with promotion recommendations.