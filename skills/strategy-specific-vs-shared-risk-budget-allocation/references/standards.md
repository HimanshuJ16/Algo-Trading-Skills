# Standards for Strategy-Specific vs Shared Risk Budget Allocation

| Metric | Standard Parameter |
|---|---|
| Euler Identity Precision | $|\sum \text{CVaR}_i - 1.0| < 10^{-4}$ ($100\%$ sum requirement). |
| VaR Confidence Level | $95.0\%$ 1-tailed Parametric VaR ($Z = 1.645$). |
| Capital Adjustment Factor | $\text{Adjustment} = \min\left(1.0, \frac{\text{Budget}}{\text{Actual Risk}}\right)$. |