# Tail Correlation Audit Checklist

- [ ] Verify return series length $\ge 20$ observations.
- [ ] Compute unconditional Pearson correlation across full sample.
- [ ] Determine empirical 10th percentile quantiles for strategy pairs.
- [ ] Evaluate lower tail dependence coefficient $\lambda_L$.
- [ ] Audit joint downside crash probability.
- [ ] Check for diversification breakdown warnings ($\rho_{\text{tail}} \ge 0.70$).
- [ ] Pass 100% unit tests.