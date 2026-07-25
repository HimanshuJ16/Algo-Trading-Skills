# Deep Workflow Reference — post-only-and-maker-taker-fee-optimization

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Maker-Taker Fee Schedule Registration**:
   - Register exchange maker fee rate (e.g. 0.05%) and taker fee rate (e.g. 0.25%).

2. **Spread-Crossing Detection & Passive Repricing**:
   - Inspect proposed limit price against `best_bid` / `best_ask`.
   - If Buy limit $\ge$ `best_ask` or Sell limit $\le$ `best_bid`, reprice to passive side (`best_bid` for Buy, `best_ask` for Sell).

3. **Post-Only Parameter Injection**:
   - Attach `post_only=True`, `time_in_force="POC"`, and `execInst="ParticipateDoNotInitiate"` to order payload.

4. **Fee Savings Quantifier**:
   - Compute USD fee savings per order: $\text{Volume} \times (\text{Rate}_{\text{taker}} - \text{Rate}_{\text{maker}})$.

## Production Implementation Reference

- Reference code: `scripts/fee_optimizer.py` (`MakerTakerFeeOptimizer`, `FeeSchedule`, `PostOnlyOrderResult`).
- Automated unit tests: `scripts/test_fee_optimizer.py`.
