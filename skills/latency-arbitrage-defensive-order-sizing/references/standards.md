# Standards for Latency Arbitrage Defense

| Metric | Engineering Standard |
|---|---|
| Sniping Probability Threshold | Sniping risk $P_{\text{snipe}} \ge 0.50$ MUST trigger quote cancellation ($Q=0$). |
| Minimum Lot Enforcement | Quote sizes below minimum lot size MUST be canceled. |
| Spread Widening | Bid-ask spread MUST widen proportionally to sniping risk ($W_{\text{spread}} = 1 + 2 P_{\text{snipe}}$). |
