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

Intersects with SEC [Rule 15c3-5](https://www.sec.gov/files/rules/final/2010/34-63241-secg.htm) (Market Access Rule — broker-dealers with market access must implement pre-trade risk controls that systematically limit financial exposure to credit/capital thresholds; US jurisdiction, mandatory for covered broker-dealers), Basel III large-exposure/concentration risk guidance (banking-sector framework, applicable indirectly), and institutional portfolio risk management standards. Parameter values above are engineering defaults, not regulatory prescriptions — map them to your firm's mandate and applicable regime.
