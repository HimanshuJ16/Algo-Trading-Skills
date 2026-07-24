# Risk Management Standards — correlation-aware-exposure-limits

| Metric / Constraint | Recommended Parameter | Operational Description |
|---|---|---|
| Correlation Threshold ($\rho_{\text{threshold}}$) | $0.70$ | Min pair correlation to form cluster |
| Rolling Lookback Window | 60 days | Historical return window for correlation estimation |
| Max Cluster Exposure Cap | $30.0\%$ NAV | Max total portfolio exposure per correlated cluster |

## Category

`risk-management` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with SEC Rule 15c3-5 risk management controls, Basel III concentration risk guidelines, and institutional portfolio risk management standards.
