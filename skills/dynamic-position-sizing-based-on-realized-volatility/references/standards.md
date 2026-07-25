# Standards — dynamic-position-sizing-based-on-realized-volatility

| Volatility Parameter | Standard Value | Rationale |
|---|---|---|
| Target Annualized Volatility ($\sigma_{\text{target}}$) | $15.0\%$ | Balanced institutional risk budget |
| Max Leverage Scalar ($\text{MaxScalar}$) | $2.0\times$ | Prevents over-leveraging in quiet regimes |
| Min Downside Scalar ($\text{MinScalar}$) | $0.20\times$ | Prevents complete zero-allocation during spikes |
| Volatility Floor ($\sigma_{\text{floor}}$) | $5.0\%$ | Prevents division by zero |

## Category

`risk-management`
