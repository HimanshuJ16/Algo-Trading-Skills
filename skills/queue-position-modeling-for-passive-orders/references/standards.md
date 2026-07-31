# Standards for Queue Position Modeling for Passive Orders

| Metric | Engineering Standard |
|---|---|
| Trade Fill Subtraction | 100% of trades at limit price MUST be subtracted from $Q_{\text{ahead}}$. |
| Cancellation Allocation | Cancellations MUST be allocated proportionally: $\alpha = \frac{Q_{\text{ahead}}}{Q_{\text{total}}}$. |
| Zero Queue Threshold | When $Q_{\text{ahead}} = 0$, our order is front-of-queue and next trade fills us. |