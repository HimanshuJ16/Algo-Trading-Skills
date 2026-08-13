---
name: adaptive-execution-under-volatility-spikes
description: Execution overlay for TWAP, VWAP, and POV schedules that fails closed during abnormal volatility by bounding participation, child size, and limit offsets before routing.
  It is an advisory decision engine; order cancellation, pre-trade risk, venue controls, and recovery remain with the execution management system.
domain: execution-algorithms
subdomain: execution-strategies
tags:
- execution
- trading
- algo
- volatility
- flash-crash
- risk-management
brokers_frameworks:
- generic
version: "1.1.0"
author: System
license: MIT
---

## When to Use

Use this overlay before each child-order decision in a TWAP, VWAP, or POV schedule when a trusted real-time feed supplies a short-horizon volatility signal. It classifies the signal into `NORMAL`, `HIGH_VOLATILITY`, or `CRITICAL_SHOCK` and returns bounded execution parameters.

The engine is deliberately advisory. It does not place, cancel, replace, or route orders and it does not enforce broker, exchange, or portfolio risk controls.

## When NOT to Use

- Do not use it as a substitute for broker or exchange pre-trade controls, trading-pause handling, limit-up/limit-down validation, or a portfolio kill switch.
- Do not use it when the volatility feed is stale, unavailable, improperly calibrated, or mixes incompatible instruments or sessions.
- Do not use it as a standalone market-impact, liquidity, best-execution, or smart-order-routing model; volatility alone does not measure spread, depth, toxicity, or venue availability.
- Do not route an order solely because this engine returns `NORMAL`; the parent scheduler and independent risk gates must still approve it.

## Prerequisites

- Python 3.10+.
- A real-time, instrument-specific volatility signal with documented units and timestamp/freshness checks performed by the caller.
- A parent execution scheduler and an EMS capable of idempotent cancel/replace requests.
- Independent pre-trade controls for quantity, notional, price collars, credit, position, and venue trading status.
- A tested operational procedure for halting, cancelling working orders, alerting, and manually or explicitly resuming a parent order.

## Workflow

1. **Validate configuration**: Construct `AdaptiveVolatilityConfig` with participation in `[0, 1]`, a positive base child size, non-negative offsets, finite thresholds, and `critical > high`.
2. **Validate the signal upstream**: Confirm instrument identity, session, timestamp freshness, and calculation method. Pass a finite numeric `current_volatility` value to `evaluate`.
3. **Evaluate before routing**: Call `AdaptiveExecutionUnderVolatilitySpikesEngine.evaluate(market_data)` for every child-order decision. Treat `MarketDataValidationError` or other validation failures as a safety event, not as normal-market input.
4. **Apply the decision**:
   - `NORMAL`: use configured participation, child size, and normal offset.
   - `HIGH_VOLATILITY`: use half the configured participation and child size, with the high-volatility offset. Re-check all EMS and venue price/quantity limits.
   - `CRITICAL_SHOCK`: do not submit new orders. Cancel all working orders for the parent using stable client/order identifiers, record the cancellation outcome, and alert operations.
5. **Recover explicitly**: Keep the parent paused after a critical shock until the external recovery policy is satisfied. Revalidate feed freshness, venue status, risk limits, and outstanding-order state before resuming; do not infer recovery from one normal observation.
6. **Observe and reconcile**: Emit regime, input timestamp, decision timestamp, parent/order identifier, cancel results, exceptions, and parameter values to an auditable event stream. Reconcile the EMS state before every resume.

## Common Pitfalls

- **Missing-data defaulting**: Treating absent volatility as `0.0` silently enables normal trading. The implementation raises `MarketDataValidationError` instead.
- **Control substitution**: Treating a strategy threshold as an exchange collar, trading halt, or regulatory control. Those controls remain outside this engine.
- **Non-idempotent cancellation**: Sending repeated cancel requests without stable identifiers or reconciling acknowledgements can leave the parent partially active.
- **Stale or cross-instrument data**: A numerically valid signal can still be unsafe when its timestamp, instrument, session, or units are wrong.
- **Threshold chatter**: A single observation below a threshold is not a sufficient recovery policy. Use an external cooldown, hysteresis, or manual release procedure.
- **Illusory liquidity**: Smaller child orders and wider offsets do not guarantee fills or prevent slippage when displayed depth disappears.

## Verification

Run the focused tests from the skill directory:

```text
python -m unittest discover -s skills/adaptive-execution-under-volatility-spikes/scripts
```

The tests cover normal, boundary, high-volatility, critical-shock, disabled, missing-input, invalid-input, mapping-type, and invalid-configuration behavior. Before deployment, replay calibrated historical and synthetic shock scenarios and verify that the EMS cancels and reconciles working orders idempotently.

## Related Skills

- `execution-algo-twap-vwap-slicing`
- `execution-algorithm-kill-switch-integration`
- `order-placement-idempotency`
- `broker-api-idempotent-cancel-requests`