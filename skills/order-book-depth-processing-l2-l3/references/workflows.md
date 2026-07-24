# Deep Workflow Reference — order-book-depth-processing-l2-l3

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Thread-Safe Mutex Lock Acquisition:**
   - Wrap all order book level additions, cancellations, and state updates in `with self._lock:`.

2. **Level 2 / Level 3 Mutation:**
   - For L2: Set price level volume. Remove price level when $V=0$.
   - For L3: Track order ID state (`add_l3_order`, `cancel_l3_order`).

3. **Crossed Book Validation:**
   - Check if $\text{Best Bid} \ge \text{Best Ask}$. If true, mark `is_crossed = True` and flag warning.

4. **Microstructure Feature Calculation:**
   - Compute Volume-Weighted Midprice:
     $$P_{\text{wmid}} = \frac{P_{\text{bid}} \cdot V_{\text{ask}} + P_{\text{ask}} \cdot V_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}$$
   - Compute Book Imbalance:
     $$I = \frac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$$

## Failure Modes Observed in Production

- **Un-Synchronized Race Conditions:** Updating bid and ask queues on separate threads without locks, producing false crossed books.
- **Dangling L3 Order Tracking:** Failing to purge canceled or filled order IDs, leading to memory leaks over multi-day sessions.

## Production Implementation Reference

- Reference code: `scripts/depth_processor.py` (`L2L3DepthProcessor`, `DepthMetrics`).
- Automated unit tests: `scripts/test_depth_processor.py`.
