# Checklist for Adaptive Execution Under Volatility Spikes

## Prerequisites

- [ ] Confirm the volatility estimator, lookback, sampling interval, session rules, units, and calibration version.
- [ ] Confirm instrument identity, feed freshness bounds, clock synchronization, and behavior for missing/stale data.
- [ ] Validate `AdaptiveVolatilityConfig` before startup and on every approved configuration reload.
- [ ] Confirm `enabled` is `True` in every production configuration, or record which independent control covers the bypass.
- [ ] Confirm the EMS applies `limit_offset_bps` away from the aggressive side (buy below, sell above the reference price), and that any opposite in-house convention is translated at the boundary.
- [ ] Confirm one engine instance per instrument and parent order; no instance is shared across symbols or threads.
- [ ] Confirm the parent scheduler and EMS expose stable client/order identifiers and parent-order correlation IDs.
- [ ] Confirm independent pre-trade controls for quantity, notional, price collars, credit, position, venue status, and trading pauses.

## Validation

- [ ] Run `python -m unittest discover -s skills/adaptive-execution-under-volatility-spikes/scripts`.
- [ ] Run invalid-input tests for missing, non-numeric, NaN, infinite, and non-mapping market data.
- [ ] Verify inclusive threshold boundaries: `high` enters `HIGH_VOLATILITY`; `critical` enters `CRITICAL_SHOCK`.
- [ ] Verify a fault following a normal tick leaves `current_regime` at `UNKNOWN`, and that monitoring/audit records the `UNKNOWN` rather than the previous `NORMAL`.
- [ ] Replay ordinary, gap, spread-widening, feed-delay, venue-pause, and flash-shock scenarios.
- [ ] Measure participation, child size, fill quality, reject rate, cancel latency, and residual working quantity by regime.

## Deployment

- [ ] Verify the engine is evaluated before every child-order decision and that exceptions stop new routing.
- [ ] Verify `halt_trading=True` gates new submissions and cancels all working parent orders in the EMS, and that callers branch on `halt_trading` before reading the zeroed numeric fields.
- [ ] Verify cancellation retries are idempotent and unknown order states keep the parent paused.
- [ ] Verify `limit_offset_bps` is checked against venue price bands and LULD/trading status before routing.
- [ ] Verify metrics, alerts, structured decision events, and audit retention are enabled.

## Rollback and Recovery

- [ ] Keep the last known-good configuration and calibration version available for an approved rollback.
- [ ] On a critical shock or feed fault, pause the parent, cancel/reconcile working orders, and page operations.
- [ ] Require fresh data, normal venue status, available risk capacity, and zero unexpected working orders before release.
- [ ] Require explicit manual or authorized automated release; never resume from a single normal observation.
- [ ] Record the release authority, reason, timestamps, and configuration version.

## Post-Deployment Verification

- [ ] Confirm the deployed package version matches the reviewed source and index entry.
- [ ] Confirm each decision includes input timestamp, regime, parameters, parent/order IDs, and calibration version.
- [ ] Review halt frequency, validation failures, cancel completion, rejects, slippage, and implementation shortfall.
- [ ] Reconcile EMS and broker/exchange order states after every halt and at end of day.
- [ ] Review threshold performance and false-positive/false-negative cases before changing calibration.