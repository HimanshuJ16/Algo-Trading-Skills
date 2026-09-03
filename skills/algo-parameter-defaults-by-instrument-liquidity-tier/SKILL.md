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
version: "1.3.0"
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
- Do not conflate these internal tiers with a regulatory liquidity classification such as ESMA's annual liquid/illiquid determination, which governs transparency obligations and not execution parameters — see `references/standards.md`.
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
2. **Decide whether freshness is mandatory**: `adv_age_days` is optional, and omitting it skips the staleness check entirely rather than failing. If the strategy cannot tolerate an unverified age, construct the manager with `require_adv_age=True` so a missing timestamp raises instead of classifying silently.
3. **Validate ADV**: Pass a finite, non-negative ADV value and, when available, its observation age. Reject stale or malformed values; do not silently classify them as `LOW`. A **zero** ADV logs a warning and is a data-quality stop, not a genuine `LOW` instrument — the manager cannot distinguish "never trades" from "the feed broke".
4. **Classify the tier**: Call `classify_tier(adv, adv_age_days=...)` or `get_profile(...)`. Threshold boundaries are deterministic: high is inclusive, medium is inclusive below high.
5. **Retrieve the profile**: Use the immutable `ExecutionProfile` as a starting constraint. Record its tier, algorithm, participation cap, buffer, and calibration version. Read the calibrated set through `manager.profiles`, a read-only mapping proxy.
6. **Read the participation cap in the right direction**: The shipped ceiling *widens* as liquidity falls (5%/10%/20%). That is a fill-feasibility allowance for orders large relative to a thin name's volume, not a discount — impact scales with `sigma * sqrt(Q/ADV)`, so the `LOW` row is the most impact-sensitive, not the most forgiving. Approach it only when completion risk demands it.
7. **Apply live gates**: Before each child order, independently check current spread/depth, volatility, order size versus displayed/expected liquidity, venue status, price bands, credit, position, and parent-order limits.
8. **Handle spread crossing**: `cross_spread_allowed` is only a profile capability. `requires_live_market_check` remains true; the EMS must make the actual crossing decision from current protected quotes and risk policy.
9. **Supply the urgency policy the `LOW` profile omits**: The `LOW` default is `IS` with `cross_spread_allowed=False`. An IS schedule front-loads to cut timing risk, which a never-cross posture cannot do, so define outside this module who may escalate to crossing, on what residual, and at what point in the horizon. Without that policy the order rests passively and accrues timing risk while every metric here still reads healthy.
10. **Monitor and tune**: Track implementation shortfall, spread capture, fill rate, participation, rejects, signaling indicators, and residual risk by tier. Retune only through versioned walk-forward/TCA review.
11. **Rollback**: If TCA, data freshness, or risk metrics breach limits, restore the last approved calibration and pause affected profiles until reconciled.

## Common Pitfalls

- **ADV unit mismatch**: Comparing shares/day, currency/day, contracts/day, or split-unadjusted volume against the same threshold. ESMA's average daily *turnover* is a currency-per-day figure and is not interchangeable with a shares/day threshold.
- **Stale ADV**: Applying a 30-day observation during a material corporate action, regime shift, or recent listing without an age/data-quality gate. Note that passing no `adv_age_days` at all disables the gate silently — `require_adv_age=True` is what closes that hole.
- **Reading the widening participation cap as permission**: Assuming a 20% ceiling in the `LOW` tier means thin names tolerate more participation. Under the square-root impact law the same fraction of ADV costs *more* there, because those names carry higher volatility.
- **An IS profile that can never cross**: Treating the `LOW` tier as a complete IS algorithm. It is a passive starting posture; the urgency escalation that makes IS an IS algorithm has to come from the integration.
- **False spread permission**: Treating high ADV as proof that crossing the spread is cheap or safe.
- **Participation overreach**: Applying a percentage cap without checking parent notional, remaining quantity, displayed depth, and market impact.
- **Mutable calibration**: Returning profiles that callers can modify and silently diverge from the reviewed calibration. Profiles are frozen, the calibrated mapping is a read-only proxy, and the caller's source mapping is copied — but a profile built directly must still be validated, which is why `ExecutionProfile` enforces its own invariants at construction.
- **Confusing a tier with a regulatory liquidity status**: An instrument ESMA classifies as having a liquid market may be `LOW` here, and the reverse. The tier is not evidence of a transparency obligation, and a transparency classification is not an execution parameter.
- **Treating a zero ADV as a tier**: Zero volume classifies as `LOW` and yields a profile whose participation cap is 20% of nothing. It is a broken or suspended instrument, not a tradable one.
- **Static tiering during shocks**: Keeping a normal profile during volatility, halts, auctions, or liquidity withdrawal.

## Verification

Run the focused tests:

```text
python -m unittest discover -s skills/algo-parameter-defaults-by-instrument-liquidity-tier/scripts
```

The tests cover tier boundaries on both sides of each threshold, invalid ADV and configuration, ADV freshness (including the inclusive age boundary and the `require_adv_age` gate), the zero-ADV warning, construction-time profile invariants, calibration versioning, immutability of both the profile and the calibrated mapping, and live-market gating metadata. Production sign-off additionally requires historical TCA, walk-forward calibration, stress scenarios, and EMS enforcement tests.

## Related Skills

- `implementation-shortfall-minimization`
- `participation-of-volume-pov-execution`
- `liquidity-adjusted-position-sizing`
- `transaction-cost-analysis-tca-integration`
- `execution-algo-twap-vwap-slicing`
