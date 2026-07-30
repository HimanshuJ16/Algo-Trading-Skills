# Standards for Cross-Venue Latency Arbitrage Defensive Design

| Metric | Engineering Standard |
|---|---|
| Toxicity Trigger | Micro-price toxicity index $\tau \ge 2.0$ ticks MUST trigger defensive quote cancellations. |
| Negative Latency Margin | $\Delta t_{\text{margin}} < 0\,\mu\text{s}$ MUST trigger immediate quote size reductions. |
| Micro-Price Weighting | Micro-price MUST incorporate real-time L1 bid/ask volume imbalance. |
