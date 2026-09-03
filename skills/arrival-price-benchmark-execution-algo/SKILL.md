---
name: arrival-price-benchmark-execution-algo
description: Implementation Shortfall (IS) execution algorithm generating optimal
  trading trajectories based on the Almgren-Chriss framework and trader urgency.
domain: execution-algorithms
subdomain: execution-strategies
tags:
- execution
- implementation-shortfall
- arrival-price
- almgren-chriss
- urgency
brokers_frameworks:
- generic
version: "1.3.0"
author: System
license: MIT
---

## When to Use

Use this skill to calculate the optimal trading schedule for executing a large parent order when the portfolio manager is being benchmarked against the **Arrival Price** — the mid-price at the exact moment the trading decision was made (industry convention: the median 1-second top-of-book mid-quote at parent-order submission time, which is statistically more stable than a single tick).

This execution algorithm minimizes **Implementation Shortfall (IS)** — the difference between the value of the trade at the arrival price and the actual capture — by balancing two competing costs:

1. **Market Impact**: Trading too fast moves the price against you.
2. **Timing Risk (Volatility)**: Trading too slow exposes the unfilled order to adverse price drift.

The schedule is the closed-form Almgren-Chriss optimal trajectory `x_t = X * sinh(kappa * (T - t)) / sinh(kappa * T)`, where `kappa` encodes the trader's risk aversion (urgency). The trade size for each bin is the drop in remaining shares across that bin, which is always non-negative and monotonically decreasing for `kappa > 0`.

Note that `1/kappa` is the trade's **half-life** and is independent of the horizon `T`. Stretching `T` at fixed urgency does not spread the order out — it appends near-empty tail bins while the leading bins stay where they were. To trade more patiently, lower the urgency; do not simply lengthen the horizon.

## When NOT to Use

- **VWAP is the actual benchmark**: If the goal is to track intraday volume distribution rather than beat the arrival price, use `execution-algo-twap-vwap-slicing` instead — an IS curve optimized against arrival price will look nothing like the market's volume profile.
- **Order is small relative to liquidity**: If a single market or aggressive limit order would not move the price meaningfully, slicing adds cost (spread crossings, fees, routing latency) with no impact benefit. Execute outright.
- **Alpha has already fully decayed**: IS execution assumes the arrival price is still the right reference; if the signal is stale, the benchmark is meaningless and a passive/reversion strategy is more appropriate.
- **Instrument is illiquid with sparse depth**: A smooth schedule assumes fillable child sizes at each bin. For micro-caps or wide-spread names, the schedule's later bins may be unfillable; route through `auction-only-order-types-for-illiquid-names` or a liquidity-seeking algo instead.
- **Multi-day parent with regime risk**: For horizons spanning days, static kappa is brittle; use `multi-day-execution-schedules-for-very-large-orders` with adaptive re-planning.

## Prerequisites

- Python 3.9+
- Total parent order size (integer shares), execution time horizon expressed as a number of equal time bins, and a defined `UrgencyLevel`.
- An **arrival price snapshot** captured at decision time (median 1-second mid-quote) and stored immutably — this is the benchmark the shortfall is measured against, not a value recomputed later.
- Order-placement infrastructure (`order-placement-idempotency`, `multi-broker-rate-limit-handling`) already in place, because slicing multiplies the number of individual order placements that each need idempotency and rate-limit discipline.
- A pre-decided **catch-up / give-up policy** for when child orders are rejected or partially filled (see Common Pitfalls).

## Workflow

1. **Capture Arrival Price**: At the instant the trading decision is made, record the median 1-second mid-quote as the immutable arrival price. This value is the IS benchmark for the entire order and must never be overwritten.
2. **Define Urgency**: The portfolio manager defines the urgency of the trade (`HIGH`, `MEDIUM`, `LOW`), which proxies the risk-aversion parameter `lambda` (and thus `kappa`) in the Almgren-Chriss framework. Map urgency to the alpha-decay horizon: minutes -> HIGH, hours -> MEDIUM, days -> LOW.
3. **Generate Trajectory**: The `ArrivalPriceTrajectoryGenerator` calculates an execution schedule (an array of integer child-order sizes per time bin) via the closed-form `sinh` solution.
   - **HIGH Urgency** (`kappa = 1.0`): steeply front-loaded — captures the arrival price quickly, accepting higher market impact to eliminate timing risk.
   - **MEDIUM Urgency** (`kappa = 0.5`): moderate front-loading, the standard Almgren-Chriss balance.
   - **LOW Urgency** (`kappa -> 0`, the linear limit): exactly uniform TWAP, minimizing immediate market impact and accepting the full timing risk.
4. **Execute Child Orders**: Route the child orders to the market according to the generated schedule, slightly randomizing timing/sizing within each bin so the pattern is not predictable to other participants.
5. **Handle Deviations**: On a rejected or partially-filled child order, apply the pre-decided catch-up/give-up policy — either redistribute the unfilled quantity across remaining bins (catch-up, more impact) or accept incomplete execution by the window's end (give-up, opportunity cost). Never blindly resubmit the exact same child order size.
6. **Measure Implementation Shortfall**: At completion, with `s = +1` for a buy and `s = -1` for a sell, compute

   ```
   IS = s * [ Q_filled * (Avg Exec Price - Arrival Price)
            + (Q_parent - Q_filled) * (Horizon End Price - Arrival Price) ] + fees
   ```

   Positive means underperformance. Note the execution term multiplies the **filled** quantity, not the parent — the unfilled remainder is an opportunity cost measured against the horizon price, which is exactly the case a give-up policy produces. Then compare against the model: `forecast_shortfall(trajectory.child_order_sizes, params, risk_aversion)` returns `E(x)`, `V(x)` and the objective `E + lambda*V`. Compare realised shortfall against `E` in units of `sqrt(V)` — `V` is in currency *squared*. A persistent excess means the impact/volatility assumptions feeding `kappa` are miscalibrated, not that the algo is broken.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Urgency/kappa calibration table and cost-model notes: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Alpha Decay**: Using `LOW` urgency for an alpha signal that decays in minutes. By the time the order finishes executing, the arrival price will have moved significantly and the "savings" from low impact are swamped by opportunity cost.
- **Over-trading Illiquid Names**: Using `HIGH` urgency on an illiquid micro-cap, causing catastrophic market impact that destroys the trade's PnL. Match urgency to the instrument's liquidity tier (`algo-parameter-defaults-by-instrument-liquidity-tier`).
- **Treating the Schedule as Inviolable**: Blindly submitting each child order at its scheduled size even when the book has thinned or the price has gapped. The schedule is a plan, not a hard contract — re-evaluate against live liquidity.
- **No Catch-up / Give-up Policy**: Leaving the behavior on rejected or partial fills undefined, discovered only when it happens live. Define the policy before trading.
- **Recomputing the Arrival Price**: Using "current mid" as the benchmark partway through execution instead of the frozen snapshot from decision time. This hides real shortfall.
- **Predictable Child-Order Patterns**: Evenly spaced, identical child orders are detectable and exploitable. Randomize within-bin timing/sizing.
- **Integer Rounding Drift**: Naive floor-and-residual apportionment can distort the curve or produce negative tail sizes. The generator uses the largest-remainder method to keep the shape intact and the sum exact.
- **Lengthening the Horizon to Trade More Patiently**: Adding bins at fixed urgency does not reduce front-loading — the half-life `1/kappa` does not depend on `T`, so the leading bins are unchanged and the extra bins are near-empty. Lower the urgency instead.
- **Comparing Realised Shortfall Against `V` Instead of `sqrt(V)`**: The Almgren-Chriss `V(x)` is a variance, in currency *squared*. Alerting on `IS > E + V` compares incommensurable units and will effectively never fire. Use `sqrt(V)` as the scale.

## Verification

- Run `python -m unittest discover -s skills/arrival-price-benchmark-execution-algo/scripts` and confirm all tests pass.
- Confirm the sum of all child orders in every generated trajectory exactly equals the parent order size (sum invariant).
- Confirm no child-order size is ever negative, including at boundary inputs (`num_bins=1`, `total_size=1`, `total_size < num_bins`, very long horizons).
- Confirm `HIGH` urgency front-loads (first bin is the maximum, first-half sum > 2x second-half sum) and the schedule is monotonically non-increasing.
- Confirm `LOW` urgency produces an exact uniform TWAP schedule.
- Confirm front-loading strictly increases LOW < MEDIUM < HIGH for the first bin.
- Confirm the same inputs always produce the same output (determinism).
- Confirm long horizons stay on the exact curve rather than degenerating: at `HIGH` urgency the first bin is ~63.2% of the parent at 10 bins and still ~63.2% at 10,000 bins (the half-life does not depend on `T`), with no overflow, `NaN`, or 100%-in-bin-0 collapse.
- Confirm `forecast_shortfall` reproduces the Almgren-Chriss limiting cases: the uniform schedule matches Eqs. (10)/(11) and the single-bin dump matches Eq. (13).
- Confirm forecast `E(x)` is strictly positive across the usable `kappa` range and that `V(x)` rises as urgency falls (front-loading buys impact to shed timing risk).
- After a live/paper execution, confirm a shortfall report comparing achieved price to the frozen arrival price is produced and reviewed, not assumed adequate.

## Related Skills

- `implementation-shortfall-minimization`
- `execution-slippage-attribution-timing-vs-sizing`
- `execution-algo-twap-vwap-slicing`
- `participation-of-volume-pov-execution`
- `algo-parameter-defaults-by-instrument-liquidity-tier`
