# Standards for Iceberg Order Detection

| Metric | Engineering Standard |
|---|---|
| Detection Volume Ratio | Cumulative traded volume MUST exceed $1.5\times$ initial display depth ($V_{\text{cum}} \ge 1.5 \times Q_0$). |
| Refill Threshold | At least 2 refill events MUST be observed at the price level. |
| Hidden Capacity Estimation | Hidden iceberg capacity MUST be estimated as $\hat{Q}_{\text{hidden}} = V_{\text{cum}} - Q_0$. |