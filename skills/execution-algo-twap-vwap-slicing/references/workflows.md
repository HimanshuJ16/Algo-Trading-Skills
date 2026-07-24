# Deep Workflow Reference — execution-algo-twap-vwap-slicing

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Benchmark & Strategy Selection:**
   - Select algorithm benchmark (`TWAP` for time-uniform distribution or `VWAP` for volume-curve matching).
   - Initialize `ExecutionSlicer` with `total_qty`, `num_intervals`, and `historical_volume_curve`.

2. **Schedule Generation & Anti-Pattern Jitter:**
   - Size child orders according to TWAP or VWAP distribution.
   - Apply randomized timing and sizing jitter ($\pm 15\%$ default) to avoid predictable execution patterns detectable by HFTs.
   - Enforce exact integer sum conservation ($\sum \text{sizes} = \text{total\_qty}$).

3. **Dynamic Child Fill Tracking & Partial Rescheduling:**
   - Track fills via `on_child_fill(slice_id, filled_qty, fill_price)`.
   - On partial fill or rejection, evaluate `CatchUpPolicy` (`AGGRESSIVE_CATCHUP`, `PASSIVE_CONTINUE`, `GIVE_UP_AT_DEADLINE`) and dynamically recalculate pending child order sizes.

4. **Rate-Limit & Idempotency Integration:**
   - Route each child order through `order-placement-idempotency` and `multi-broker-rate-limit-handling` wrappers to prevent duplicate order placement during slicing.

5. **Post-Execution Slippage & Benchmark Reporting:**
   - Call `get_execution_report(benchmark_price)` after execution completion.
   - Compute VWAP achieved price ($\bar{P}_{\text{achieved}}$) and Implementation Shortfall / Slippage in basis points relative to benchmark.

## Failure Modes Observed in Production

- **Deterministic Slicing Pattern:** Placing child orders at exact 60-second boundaries with identical lot sizes, allowing predatory algorithms to front-run execution.
- **Static Schedule Disconnect:** Failing to adjust remaining child order sizes when early orders suffer partial fills or rejections.
- **Unmeasured Slippage:** Failing to report achieved execution price against TWAP/VWAP benchmark, masking execution drag.

## Production Implementation Reference

- Reference code: `scripts/slicer.py` (`ExecutionSlicer`, `ChildOrderSlice`, `SlicerType`, `CatchUpPolicy`, `ExecutionReport`).
- Automated unit tests: `scripts/test_slicer.py`.
