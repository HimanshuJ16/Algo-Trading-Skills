# Standards — adjusted-vs-unadjusted-price-series-pitfalls

| Corporate Action | Price Adjustment | Volume Adjustment | Risk if mishandled |
|---|---|---|---|
| **Stock Split (Forward)** | Divide by ratio | Multiply by ratio | False -50% price drop signals |
| **Stock Split (Reverse)** | Multiply by ratio | Divide by ratio | False +100% price spike signals |
| **Cash Dividends** | None (Track as Cash) | None | Backward-adjusting causes Look-Ahead Bias |
| **Universe Rule** | All symbols must match | All symbols must match | Cross-asset correlation corruption |

## Category
`backtesting-methodology`
