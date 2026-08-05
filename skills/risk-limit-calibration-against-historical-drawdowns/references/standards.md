# Standards for Risk Limit Calibration Against Historical Drawdowns

| Metric | Engineering Standard |
|---|---|
| Minimum History Window | Minimum 252 trading days (1 year) of return history required for production limit calibration. |
| Stress Buffer Multiplier | Calibrated drawdown limit MUST apply $\ge 1.5\times$ multiplier over historical max DD. |
| Daily Loss Limit | Daily loss limit MUST be set to $\le 3\times$ daily 99% VaR in USD. |