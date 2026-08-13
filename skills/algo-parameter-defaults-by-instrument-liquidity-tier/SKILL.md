---
name: algo-parameter-defaults-by-instrument-liquidity-tier
description: Assign validated, versioned execution starting profiles from instrument liquidity tiers while requiring fresh market-state and independent risk checks before routing.
  ADV thresholds are calibration inputs, not universal market rules or permission to cross a spread.
domain: algorithmic-trading
subdomain: execution-algorithms
tags:
- execution
- smart-order-routing
- market-impact
- twap
- vwap
brokers_frameworks:
- generic
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this skill when initializing VWAP, TWAP, or Implementation Shortfall execution with instrument-specific starting constraints. The manager classifies a validated Average Daily Volume (ADV) observation into `HIGH`, `MEDIUM`, or `LOW` and returns an immutable, versioned `ExecutionProfile`.

The profiles are defaults and caps for a downstream execution system. They are not a live liquidity model, best-execution decision, venue permission, or substitute for pre-trade risk controls.

## When NOT to Use

- Do not classify an instrument from stale, mixed-unit, split-affected, or unverified ADV data.
- Do not use ADV alone to authorize spread crossing, market orders, participation, or urgency; current spread, depth, volatility, order size, venue state, and risk limits are required.
- Do not use static tiers for intraday liquidity migration, news events, auctions, halts, or flash volatility without a separate market-state overlay.
- Do not treat the illustrative 5%/10%/20% defaults as regulatory, venue, or universal institutional limits.
- Do not bypass independent market-access, price-band, credit, position, notional, or kill-switch controls.

## Prerequisites

- A documented ADV definition: instrument, units, session calendar, corporate-action treatment, lookback, and as-of timestamp.
- A configured high/medium threshold pair with `high > medium` and a maximum permitted ADV age.
- Current market-state data for spread, depth, volatility, venue status, and order-size impact checks.
- An EMS that can enforce participation, price, notional, credit, position, and cancellation controls independently.
- A versioned calibration process using TCA, walk-forward evaluation, and rollback approvals.
- Python 3.10+.

## Workflow

1. **Define calibration**: Set validated ADV thresholds, `max_adv_age_days`, profile values, and a calibration version. Store the unit and factor source outside the manager.
2. **Validate ADV**: Pass a finite, non-negative ADV value and, when available, its observation age. Reject stale or malformed values; do not silently classify them as `LOW`.
3. **Classify the tier**: Call `classify_tier(adv, adv_age_days=...)` or `get_profile(...)`. Threshold boundaries are deterministic: high is inclusive, medium is inclusive below high.
4. **Retrieve the profile**: Use the immutable `ExecutionProfile` as a starting constraint. Record its tier, algorithm, participation cap, buffer, and calibration version.
5. **Apply live gates**: Before each child order, independently check current spread/depth, volatility, order size versus displayed/expected liquidity, venue status, price bands, credit, position, and parent-order limits.
6. **Handle spread crossing**: `cross_spread_allowed` is only a profile capability. `requires_live_market_check` remains true; the EMS must make the actual crossing decision from current protected quotes and risk policy.
7. **Monitor and tune**: Track implementation shortfall, spread capture, fill rate, participation, rejects, signaling indicators, and residual risk by tier. Retune only through versioned walk-forward/TCA review.
8. **Rollback**: If TCA, data freshness, or risk metrics breach limits, restore the last approved calibration and pause affected profiles until reconciled.

## Common Pitfalls

- **ADV unit mismatch**: Comparing shares/day, currency/day, contracts/day, or split-unadjusted volume against the same threshold.
- **Stale ADV**: Applying a 30-day observation during a material corporate action, regime shift, or recent listing without an age/data-quality gate.
- **False spread permission**: Treating high ADV as proof that crossing the spread is cheap or safe.
- **Participation overreach**: Applying a percentage cap without checking parent notional, remaining quantity, displayed depth, and market impact.
- **Mutable calibration**: Returning profiles that callers can modify and silently diverge from the reviewed calibration.
- **Static tiering during shocks**: Keeping a normal profile during volatility, halts, auctions, or liquidity withdrawal.

## Verification

Run the focused tests:

```text
python -m unittest discover -s skills/algo-parameter-defaults-by-instrument-liquidity-tier/scripts
```

The tests cover tier boundaries, invalid ADV and configuration, ADV freshness, custom profile validation, calibration versioning, immutable profiles, and live-market gating metadata. Production sign-off additionally requires historical TCA, walk-forward calibration, stress scenarios, and EMS enforcement tests.

## Related Skills

- `implementation-shortfall-minimization`
- `participation-of-volume-pov-execution`