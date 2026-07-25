# Deep Workflow Reference — multi-account-same-strategy-fan-out

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Sub-Account Ingestion & NAV Weighting**:
   - Register sub-account IDs and individual NAV values.
   - Calculate account allocation weight $w_i = \text{NAV}_i / \sum \text{NAV}$.

2. **Master Signal Pro-Rata Transformation**:
   - Compute sub-account target quantity $Q_i = \text{round}(Q_{\text{master}} \times w_i)$.
   - Enforce `min_order_qty` threshold for small sub-accounts.

3. **Collision-Free Client Order ID Generation**:
   - Generate unique client order ID for each sub-account order: `CLORD_{account_id}_{timestamp}_{seq}`.

4. **Concurrent Order Dispatch & Fill Aggregation**:
   - Dispatch sub-account orders in parallel to prevent execution latency drift across client accounts.

## Production Implementation Reference

- Reference code: `scripts/fanout_engine.py` (`MultiAccountStrategyFanOut`, `AccountOrder`, `FanOutReport`).
- Automated unit tests: `scripts/test_fanout_engine.py`.
