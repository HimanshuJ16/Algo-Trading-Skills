# Standards for Multi-Leg Atomicity

| Metric | Rule |
|--------|------------|
| Anchor Leg Selection | Always the instrument with the widest Bid/Ask spread in bps. |
| Hedging Leg Order Type | IOC (Immediate or Cancel) or FOK (Fill or Kill). Never Market. |
| Slippage Tolerance | Maximum acceptable loss on the hedging leg must be explicitly defined (e.g., $\le 5$ bps). |
| Partial Fills | The hedging leg order size must dynamically adjust to match the *exact executed quantity* of the anchor leg. |