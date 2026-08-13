# Workflows for Adaptive Execution Under Volatility Spikes

## Production Execution Pipeline

1. **Initialize**: Load a versioned `AdaptiveVolatilityConfig`; validate it at startup and fail deployment if validation fails.
2. **Ingest and validate market data**: For each instrument, verify symbol/session identity, source health, timestamp freshness, sampling interval, and estimator units before producing `current_volatility`. Never convert missing, stale, or malformed data to zero.
3. **Evaluate before every child order**:
   ```python
   try:
       parameters = engine.evaluate({"current_volatility": volatility_zscore})
   except (MarketDataValidationError, TypeError, ValueError):
       stop_parent_and_cancel_working_orders(parent_id)
       raise
   ```
4. **Apply parameters only after independent controls pass**:
   - Use `participation_rate` against the current parent/schedule volume budget.
   - Cap `child_order_size` by remaining parent quantity, instrument lot size, notional/risk limits, and venue minimums.
   - Apply `limit_offset_bps` only through an EMS price-band and LULD/trading-status check; do not treat it as a venue permission.
   - Attach stable client order identifiers and preserve the parent-order correlation ID.
5. **Handle high volatility**: Reduce new child order rate and size as returned. Continue monitoring spread, depth, reject rate, fill quality, and feed freshness; volatility reduction alone does not prove liquidity is executable.
6. **Handle critical shock**: If `halt_trading` is true, atomically gate new submissions, cancel all working child orders for the parent, retry idempotently where permitted, and reconcile acknowledgements. Emit an alert with the input and decision timestamps.
7. **Recover explicitly**: Keep the parent paused until the external recovery policy confirms fresh data, normal venue status, completed cancellation/reconciliation, and available risk capacity. Require a manual or explicitly authorized release after a critical shock.
8. **Persist an audit trail**: Store configuration/calibration version, raw signal, source timestamp, returned regime and parameters, parent/order identifiers, submit/cancel outcomes, exceptions, and resume authorization.

## Failure Handling Matrix

| Failure | Engine behavior | Required integration behavior |
|---|---|---|
| Missing `current_volatility` | Raises `MarketDataValidationError` | Stop new routing, cancel/reconcile working orders, page operations. |
| Non-numeric, NaN, or infinite volatility | Raises `MarketDataValidationError` | Treat as feed/data fault; do not retry as normal input. |
| Invalid configuration | Raises `TypeError`/`ValueError` at construction or evaluation | Reject startup/config reload; retain last known-good config only if explicitly approved. |
| `CRITICAL_SHOCK` | Returns zero-size, zero-participation halt decision | Gate submissions, cancel/reconcile, alert, and require explicit release. |
| Venue pause or LULD rejection | Not detected by this engine | Enforce venue state and price-band controls in EMS/broker adapter. |
| Cancel timeout or unknown order state | Not detected by this engine | Keep parent paused and reconcile through broker/exchange order state. |

## Backtest and Replay Workflow

1. Calibrate the volatility estimator out of sample by instrument and session; record the lookback, sampling interval, and threshold version.
2. Replay ordinary, gap, spread-widening, feed-delay, venue-pause, and flash-shock scenarios with realistic order-book and reject behavior.
3. Assert that boundary values classify deterministically (`high` is high volatility; `critical` is critical shock).
4. Measure participation, child-size, fill rate, implementation shortfall, cancel latency, reject rate, and residual working quantity by regime.
5. Verify the EMS integration separately: repeated halt events must not duplicate cancels, and resumption must not occur with unknown working orders.
6. Approve deployment only when the scenario results and calibration version are recorded and independently reviewed.