# Standards for Real-Time VaR Backtesting Kupiec Test

| Metric | Engineering Standard |
|---|---|
| Regulatory Observation Window | Backtesting MUST cover at least $T = 250$ trading days (1 year). |
| $99\%$ VaR Expected Exception Rate | Target exception rate MUST equal $p = 1 - 0.99 = 0.01$ ($1\%$). |
| Significance Threshold | Model rejection MUST occur when $p\text{-value} < 0.05$ ($5\%$ significance). |
