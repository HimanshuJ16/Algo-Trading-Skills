# Standards — backtest-outlier-and-bad-tick-filtering

| Outlier Metric | Recommended Limit | Action on Breach |
|---|---|---|
| Modified Z-Score Threshold | $\le 5.0$ MAD | Purge price print |
| Single Tick Jump | $\le 20.0\%$ | Purge bad print |
| Non-Positive Price | $P \le 0$ | Immediately reject |

## Category

`backtesting-methodology`
