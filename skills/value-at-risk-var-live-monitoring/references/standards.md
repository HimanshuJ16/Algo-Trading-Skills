# Risk Management Standards — value-at-risk-var-live-monitoring

| Risk Metric | Confidence Level / Parameter | Description |
|---|---|---|
| Parametric VaR | $99\%$ Confidence ($Z = 2.326$) | Variance-covariance loss threshold assuming normality |
| Historical Simulation VaR | $99\%$ Quantile ($1\text{st}$ percentile) | Empirical historical distribution loss cutoff |
| Conditional VaR (CVaR) | Tail Expected Shortfall | Expected loss severity conditional on breaching VaR |
| Max 1-Day VaR Limit | $5.0\%$ of NAV | Hard circuit breaker limit to block new order entries |

## Category

`risk-management` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with Basel III market risk framework, SEC Rule 15c3-5 risk controls, and institutional portfolio risk limits.
