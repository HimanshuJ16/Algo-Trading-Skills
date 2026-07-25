# Real-Time Architecture Standards — order-book-imbalance-signal-pipeline

| Signal Metric | Formula | Threshold Trigger |
|---|---|---|
| Order Book Imbalance ($I$) | $\frac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$ | $I \ge +0.60$ (Buy), $I \le -0.60$ (Sell) |
| Micro-Price ($P_{\text{micro}}$) | $\frac{V_{\text{bid}} P_{\text{ask}} + V_{\text{ask}} P_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}$ | Price divergence from mid |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
