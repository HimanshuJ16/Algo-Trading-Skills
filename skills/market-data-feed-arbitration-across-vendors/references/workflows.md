# Deep Workflow Reference — market-data-feed-arbitration-across-vendors

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Ingest Dual Vendor Ticks**:
   - Receive parallel tick updates from Primary and Secondary vendor feeds with high-precision timestamps.

2. **Check Vendor Staleness**:
   - Inspect elapsed time since last tick for each vendor. If latency $> 2.0\text{s}$, flag vendor as `STALE_TIMEOUT` and fail over.

3. **Calculate Price Divergence**:
   - Compute relative divergence $\delta = \frac{|P_A - P_B|}{\text{avg}(P_A, P_B)} \times 100\%$.

4. **Arbitrate Consensus or Quarantine Outlier**:
   - If $\delta \le 0.05\%$: Emit average consensus price.
   - If $\delta > 0.05\%$: Flag outlier feed as `DIVERGENT_OUTLIER` and default to Primary feed.

## Production Implementation Reference

- Reference code: `scripts/feed_arbitrator.py` (`MarketDataFeedArbitrator`, `ArbitratedTickResult`, `VendorStatus`).
- Automated unit tests: `scripts/test_feed_arbitrator.py`.
