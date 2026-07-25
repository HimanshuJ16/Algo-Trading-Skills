# Backtesting Methodology Standards — transaction-cost-analysis-tca-integration

| TCA Component | Calculation Formula | Impact Threshold |
|---|---|---|
| Delay Cost | $\frac{P_{\text{arrival}} - P_{\text{decision}}}{P_{\text{decision}}}$ | High for latency-sensitive signals |
| Half Spread Cross | $\frac{0.5 \cdot \text{Spread}}{P_{\text{decision}}}$ | Constant taker fee penalty |
| Market Impact | $\gamma \cdot \sqrt{\frac{\text{OrderSize}}{\text{ADV}}}$ | Non-linear growth for large orders ($>1\%$ ADV) |

## Category

`backtesting-methodology` — see top-level `mappings/` directory.
