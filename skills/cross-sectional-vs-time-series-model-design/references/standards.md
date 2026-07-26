# Standards for Cross-Sectional vs Time-Series Model Design

| Metric | Engineering Standard |
|---|---|
| Dollar Neutrality | Cross-sectional weights MUST satisfy $\sum w_{i,t} = 0.0 \pm 10^{-5}$ at every timestamp. |
| Volatility Targeting | Time-series positions MUST be scaled by annualized target volatility $\sigma_{target}$. |
| Outlier Windsorization | Raw alpha factors MUST be windsorized at $\pm 3.0 \sigma$ prior to Z-score calculation. |
