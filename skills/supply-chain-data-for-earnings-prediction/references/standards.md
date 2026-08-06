# Standards for Supply Chain Data for Earnings Prediction

| Metric | Standard Parameter |
|---|---|
| Lead Lag Horizon | 1 to 3 months (quarterly lead). |
| Weighting Ratio | Upstream Supplier: $0.70$, Customer Inventory: $-0.30$. |
| Surprise Threshold | $|Z| \ge 1.0$ (normalized by $5\%$ std dev). |
| Signal Output | `BUY_EARNINGS_SURPRISE`, `SELL_EARNINGS_DISAPPOINTMENT`, `NEUTRAL`. |