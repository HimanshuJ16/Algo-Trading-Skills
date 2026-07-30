# Standards for Google Trends Signal Research

| Metric | Engineering Standard |
|---|---|
| Availability Lag | SVI data MUST be shifted by $\ge 24\text{ hours}$ to prevent lookahead bias. |
| Z-Score Threshold | Attention surge MUST require SVI Z-score $Z_t \ge 2.0$. |
| Rolling Lookback Window | Rolling statistics MUST use at least $N = 30$ periods. |