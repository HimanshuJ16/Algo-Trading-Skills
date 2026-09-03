---
name: adaptive-execution-under-volatility-spikes
description: >-
  Use before each child-order decision in a TWAP, VWAP or POV schedule when a volatility
  spike should bound participation, child size and limit offset; fails closed when the
  volatility feed is stale or missing.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: execution, trading, algo, volatility, flash-crash, risk-management
  brokers_frameworks: generic
  version: "1.2.0"
  author: algo-trading-skills-contributors
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

1. **Validate configuration**: Construct `AdaptiveVolatilityConfig` with participation in `[0, 1]`, a positive base child size, non-negative offsets, finite thresholds, and `critical > high`. Leave `enabled=True`: `enabled=False` is a bypass that returns the base parameters and skips volatility validation entirely, not a safe default. The engine re-validates the config on every call, so a live edit or partial reload fails closed rather than routing on out-of-range bounds.
2. **Validate the signal upstream**: Confirm instrument identity, session, timestamp freshness, and calculation method. Pass a finite numeric `current_volatility` value to `evaluate`.
3. **Evaluate before routing**: Call `AdaptiveExecutionUnderVolatilitySpikesEngine.evaluate(market_data)` for every child-order decision, using one engine instance per instrument and parent order. Treat `MarketDataValidationError` or other validation failures as a safety event, not as normal-market input; a failed evaluation leaves `engine.current_regime` at `UNKNOWN` rather than at the regime of the last successful call, and a newly constructed engine reports `UNKNOWN` until an evaluation succeeds.
4. **Apply the decision**: branch on `halt_trading` before reading any numeric field.
   - `NORMAL`: use configured participation, child size, and normal offset.
   - `HIGH_VOLATILITY`: use half the configured participation and child size, with the high-volatility offset. Re-check all EMS and venue price/quantity limits.
   - `CRITICAL_SHOCK`: do not submit new orders. Cancel all working orders for the parent using stable client/order identifiers, record the cancellation outcome, and alert operations.

   `limit_offset_bps` is a distance *away from the aggressive side* of your reference price — a buy limit at `ref * (1 - bps / 10_000)`, a sell limit at `ref * (1 + bps / 10_000)`. The larger high-volatility offset is therefore the more passive one. Map the sign explicitly if your EMS defines offsets as aggressiveness.
5. **Recover explicitly**: Keep the parent paused after a critical shock until the external recovery policy is satisfied. Revalidate feed freshness, venue status, risk limits, and outstanding-order state before resuming; do not infer recovery from one normal observation.
6. **Observe and reconcile**: Emit regime, input timestamp, decision timestamp, parent/order identifier, cancel results, exceptions, and parameter values to an auditable event stream. Reconcile the EMS state before every resume.

## Common Pitfalls

- **Missing-data defaulting**: Treating absent volatility as `0.0` silently enables normal trading. The implementation raises `MarketDataValidationError` instead.
- **Control substitution**: Treating a strategy threshold as an exchange collar, trading halt, or regulatory control. Those controls remain outside this engine.
- **Non-idempotent cancellation**: Sending repeated cancel requests without stable identifiers or reconciling acknowledgements can leave the parent partially active.
- **Stale or cross-instrument data**: A numerically valid signal can still be unsafe when its timestamp, instrument, session, or units are wrong.
- **Threshold chatter**: A single observation below a threshold is not a sufficient recovery policy. Use an external cooldown, hysteresis, or manual release procedure.
- **Inverted offset convention**: Applying `limit_offset_bps` as aggressiveness rather than passivity makes the high-volatility branch chase a dislocating book — the exact opposite of the intended protection. The halt decision zeroes every numeric field, so a caller that ignores `halt_trading` and prices off those zeros gets the most aggressive offset available.
- **Bypass mistaken for a default**: `enabled=False` disables the volatility validation along with the overlay. A `NORMAL` result from a disabled engine says nothing about the market; it was never measured.
- **Shared engine instances**: `current_regime` describes that instance's last evaluation only. One engine shared across symbols or parents reports whichever evaluated most recently, and concurrent calls on one instance race. Read `ExecutionParameters.regime` from the returned object for a per-decision value.
- **Illusory liquidity**: Smaller child orders and wider offsets do not guarantee fills or prevent slippage when displayed depth disappears.

## Verification

Run the focused tests from the skill directory:

```text
python -m unittest discover -s skills/adaptive-execution-under-volatility-spikes/scripts
```

The tests cover normal, boundary, negative-signal, high-volatility, child-size-floor, critical-shock halt-sentinel, disabled-bypass, missing-input, invalid-input, mapping-type, constructor-type, runtime config-mutation, and post-fault `UNKNOWN` state behavior. Before deployment, replay calibrated historical and synthetic shock scenarios and verify that the EMS cancels and reconciles working orders idempotently.

## Related Skills

- `execution-algo-twap-vwap-slicing`
- `execution-algorithm-kill-switch-integration`
- `order-placement-idempotency`
- `broker-api-idempotent-cancel-requests`