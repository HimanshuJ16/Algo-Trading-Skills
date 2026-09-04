---
name: implementation-shortfall-minimization
description: >-
  Use when scheduling a large parent order, or measuring what one cost after the fact.
  Shortfall is measured against the decision price, not arrival; Almgren-Chriss
  trajectories by urgency, arrival-price capture, and the four-component Perold split.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: implementation-shortfall, almgren-chriss, arrival-price, perold-tca, transaction-cost-analysis, market-impact, opportunity-cost, basis-points
  brokers_frameworks: "Almgren-Chriss (2000); Perold (1988) TCA; Python Dataclasses"
  version: "2.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when scheduling a large parent order across intervals, or when measuring the full economic cost of one after the fact. Implementation Shortfall (IS) is the return difference between the **paper portfolio** — the whole order filled instantly at the **Decision Price $P_0$** — and the portfolio actually implemented. It is the only common execution benchmark that charges you for the shares you *failed* to trade, which is why a VWAP-beating algorithm can still post a terrible shortfall.

Four capabilities, usable independently:

- **Benchmark capture**: `median_mid_arrival_price` reduces the one-second window of top-of-book quotes at parent-order submission to the **arrival price $P_a$** — the industry-convention median mid, not a single tick. Capture it once and store it immutably.
- **Pre-trade schedule**: `calculate_almgren_chriss_trajectory` returns whole shares to work per interval, following the closed-form Almgren-Chriss (2000) holdings trajectory $x_j = X \sinh(\kappa(T-t_j))/\sinh(\kappa T)$ (their Eq. 17).
- **Pre-trade cost forecast**: `forecast_shortfall` prices that schedule under the same linear-impact model, returning $E(x)$, $V(x)$ and the objective $E + \lambda V$ — the yardstick the realised shortfall is later read against.
- **Post-trade measurement**: `evaluate_implementation_shortfall` decomposes the order into execution cost, opportunity cost and explicit fees — and, when an arrival price is supplied, splits the executed leg into delay and market impact.

**Cost sign convention: positive is money lost**, for buys and sells alike.

## When NOT to Use

- **As a calibrated impact model out of the box.** $\kappa$ is a function of $\lambda\sigma^2/\tilde\eta$, not of $\lambda$ alone. The `volatility_per_sqrt_time` / `temporary_impact_eta` / `permanent_impact_gamma` defaults are dimensionless placeholders (1, 1, 0) describing no instrument. Supply $\sigma$ and $\eta$ estimated for the name being traded, in units consistent with `interval_length`, or treat the output as an abstract urgency dial and say so.
- **To measure market impact causally.** The impact term is whatever the price did between arrival and completion. It contains market drift and news alongside your order's footprint, and no post-trade arithmetic separates them — the price path had you not traded is unobservable. Never feed the result back in as $\eta$.
- **When VWAP or POV is the actual benchmark.** An IS-optimal curve looks nothing like a volume profile — see `execution-algo-twap-vwap-slicing` and `participation-of-volume-pov-execution`.
- **When the order is small relative to liquidity.** If a single marketable order would not move the price meaningfully, slicing adds spread crossings, fees and routing latency for no impact benefit. Execute outright.
- **When the alpha has already decayed.** IS execution assumes the decision-time price is still the right reference; if the signal is stale the benchmark is meaningless and a passive/reversion approach fits better.
- **On an illiquid name with sparse depth.** A smooth schedule assumes each interval's child size is fillable. For micro-caps or wide-spread names the tail intervals may be unfillable — route through `auction-only-order-types-for-illiquid-names` or a liquidity-seeking algo, and size urgency to the liquidity tier (`algo-parameter-defaults-by-instrument-liquidity-tier`).
- **On a multi-day parent with regime risk.** A static $\kappa$ is brittle across days; use `multi-day-execution-schedules-for-very-large-orders` with adaptive re-planning.
- **To attribute the executed leg alone.** Delay-vs-impact triage, materiality thresholds and driver naming belong to `execution-slippage-attribution-timing-vs-sizing`; this skill reports the four-component total.
- **On multi-currency, multi-day or parent-of-parent orders.** One order, one currency, one horizon. Convert to a single currency before calling.
- **As a live risk control.** Nothing here caps exposure or halts trading; see `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- **Decision Price $P_0$** — the price at the moment the PM decided, not when the algo started. If only the arrival price is available, say so; do not silently substitute it.
- Parent quantity $Q$, `side`, and the executed fills ($q_k$, $P_k$, fee$_k$, unique `fill_id`).
- **Horizon price $P_{\text{final}}$** for marking unexecuted shares, under a fixed, documented horizon convention (order-cancel time, or close).
- Optional **arrival price $P_a$** to split delay from impact — the median top-of-book mid over the one-second window at submission, captured once and **stored immutably**. It is never inferred, and never recomputed mid-execution.
- For a calibrated schedule: $\sigma$ in price units per $\sqrt{\text{time}}$, temporary impact $\eta$, permanent impact $\gamma$, and interval length $\tau$ — with $\sigma$ and $\tau$ expressed in the **same** time unit ($\tau = 1$ means $\sigma$ is per-$\sqrt{\text{interval}}$). For a cost forecast, additionally the fixed per-share cost $\epsilon$ (half-spread plus fees).
- Order-placement infrastructure (`order-placement-idempotency`, `multi-broker-rate-limit-handling`) already in place — slicing multiplies the number of child placements, each needing its own idempotency and rate-limit discipline.
- A pre-decided **catch-up / give-up policy** for rejected or partially filled child orders.

## Workflow

0. **Capture the arrival-price benchmark at submission** — `median_mid_arrival_price`:
   - The convention is the **median top-of-book mid over the one-second window** at parent-order submission. A single tick is one draw from the quote-flicker distribution, and one stale or crossed print relocates the whole benchmark; the median over ~1s is robust to both.
   - **Decision point — freeze it.** Store the value immutably with the order. Recomputing "current mid" partway through execution is the classic way to make a shortfall report show no shortfall: the benchmark chases the price the order is itself moving.
   - A crossed quote ($\text{bid} > \text{ask}$) raises rather than being averaged in — it is a feed artefact or a genuine dislocation, and either way it is resolved upstream, not smoothed into the number the desk is graded on. A locked market ($\text{bid} = \text{ask}$) is accepted.
   - $P_a$ is **not** $P_0$. The gap between them *is* the delay cost; substituting one for the other deletes it.

1. **Compute $\kappa$ before generating any schedule** — `almgren_chriss_kappa`:
   $$\tilde\eta = \eta - \frac{\gamma\tau}{2}, \qquad \tilde\kappa^2 = \frac{\lambda\sigma^2}{\tilde\eta}, \qquad \kappa = \frac{1}{\tau}\operatorname{arccosh}\!\left(1 + \frac{\tilde\kappa^2\tau^2}{2}\right)$$
   - **Decision point — this is the exact discrete root of Almgren-Chriss Eq. (16), not their Eq. (19) small-$\tau$ approximation $\kappa \approx \sqrt{\lambda\sigma^2/\eta}$.** On a coarse interval grid the two differ materially; use the exact root for the grid you actually trade.
   - **Decision point — if $\tilde\eta \le 0$ the problem is degenerate**, not merely aggressive: permanent impact over one interval has reached temporary impact and Eq. (16) has no real decay root. The engine raises. Shorten $\tau$ or re-estimate the coefficients; do not clamp.
   - $\lambda = 0$ is risk-neutral and gives exact TWAP. $\lambda < 0$ raises — it is an ill-posed problem, not a slower schedule.

2. **Generate the trajectory** — `calculate_almgren_chriss_trajectory`:
   - **Decision point — round the holdings path, not the slices.** Slices are the differences of a monotone-rounded holdings trajectory, so every slice is $\ge 0$ and they sum to $Q$ exactly. Rounding each slice independently and plugging the residual into the last interval can emit a *negative* slice — a reversing trade Almgren-Chriss never prescribes ("we have $n_j > 0$ for each $j$ as long as $X > 0$", ibid. Sec. 3).
   - Higher $\lambda$ front-loads to cut timing risk; lower $\lambda$ flattens toward TWAP to cut impact. Map urgency to the alpha-decay horizon: a signal decaying in minutes wants a high $\lambda$, one decaying over days a low one.
   - **Decision point — do not lengthen the horizon to trade more patiently.** The half-life $1/\kappa$ does not depend on $T$ (ibid., Sec. 2.3), so adding intervals at fixed urgency leaves the leading intervals exactly where they were and appends near-empty tail intervals. At $\kappa = 1$ the first interval is $1 - e^{-1} = 63.2\%$ of the parent whether the horizon is 10 intervals or 10,000. Lower $\lambda$ instead.

2a. **Forecast the schedule's cost** — `forecast_shortfall(schedule, ImpactParameters(...), risk_aversion)`:
   - Returns $E(x) = \frac{\gamma X^2}{2} + \epsilon\sum|n_k| + \frac{\tilde\eta}{\tau}\sum n_k^2$ (Eq. 8) and $V(x) = \sigma^2\tau\sum_{k\ge1} x_k^2$ (Eq. 5), summed over the **integer** schedule actually being sent rather than from their closed-form Eq. (20), which is numerically awkward at small or large $\kappa T$.
   - **Decision point — compare realised shortfall against $E$ in units of `stdev`, never `variance`.** $V$ is in currency *squared*; an alert written as $\text{IS} > E + V$ compares incommensurable units and will effectively never fire. A persistent excess over $E + k\,\sigma_{\text{IS}}$ means the impact and volatility assumptions feeding $\kappa$ are miscalibrated, not that the algo is broken.
   - `ImpactParameters` rejects $\tilde\eta \le 0$ for the same reason `almgren_chriss_kappa` does: outside that range the cost functional is not convex and the "optimal" trajectory is not a minimiser.

3. **Route the child orders and handle deviations**: work the schedule, randomising timing and sizing slightly within each interval so the pattern is not exploitable. The schedule is a plan, not a contract — re-evaluate against live depth. On a rejected or partially filled child order apply the pre-decided **catch-up** (redistribute the remainder across the remaining intervals, more impact) or **give-up** (accept an incomplete execution, more opportunity cost) policy; never blindly resubmit the same child size.

4. **Ingest fills and validate before measuring**:
   - **Decision point — if executed quantity exceeds $Q$, stop.** IS is undefined against a paper portfolio smaller than the real one. An over-fill is an order-control incident (`order-placement-idempotency`), not a TCA result; the engine raises rather than clamping the unfilled quantity to zero.
   - Duplicate `fill_id`, non-finite or non-positive prices, non-positive quantities and non-finite fees all raise. A single NaN otherwise yields a NaN shortfall stamped `IS_EVALUATION_SUCCESS`.

5. **Decompose (Perold 1988)**, with $s = +1$ for a buy and $-1$ for a sell:
   - **Execution cost**: $s\sum_k q_k (P_k - P_0)$ — delay *plus* impact *plus* market drift on filled shares.
   - **Delay / impact split** (only when $P_a$ is given): $s\,Q_f(P_a - P_0)$ and $s\sum_k q_k(P_k - P_a)$. Additive by construction, so the total does not move.
   - **Opportunity cost**: $s\,(Q - Q_f)(P_{\text{final}} - P_0)$.
   - **Explicit fees**: commissions and exchange fees; negative for a maker rebate.
   - **Total**:
     $$\text{IS}_{\text{bps}} = \frac{\text{IS}_{\text{total}}}{Q \times P_0} \times 10{,}000$$
   - **Decision point — the denominator is the *intended* notional $Q \times P_0$**, not the executed notional. On a 10%-filled order the executed notional makes a costly miss look cheap.

6. **Read the status field**: `IS_EVALUATION_NO_FILLS` means nothing traded — the shortfall is 100% opportunity cost and `volume_weighted_executed_price` is `None`, never $P_0$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting the executed-leg cost as "market impact"**: $\sum q_k(P_k - P_0)$ is delay + impact + drift. Calling it impact sends the desk to slow the algorithm down when the real cost was a slow dispatch path, or the market simply moving. Without an arrival price this engine reports `execution_cost_usd` and leaves `market_impact_cost_usd` as `None` rather than mislabelling it.
- **Ignoring opportunity cost of unfilled shares**: judging execution on filled shares alone rewards an algorithm that quietly stops trading when the price runs away from it. That is precisely the behaviour Perold's shortfall exists to penalise.
- **Using arrival price instead of decision price as $P_0$**: benchmarking against the price when the algo started, not when the PM decided, deletes the entire delay cost — usually the component a trading desk can actually fix.
- **Recomputing the arrival price mid-flight**: using "current mid" partway through execution instead of the frozen snapshot from submission makes the benchmark chase the price the order is itself moving, and the report shows no shortfall. Capture once, store immutably.
- **Taking the arrival price from a single tick**: one print is a draw from the quote-flicker distribution, and a stale or crossed quote relocates the entire benchmark. Use the one-second median mid.
- **Lengthening the horizon to trade more patiently**: adding intervals at fixed urgency does not reduce front-loading — the half-life $1/\kappa$ is independent of $T$, so the leading intervals are unchanged and the extra ones are near-empty. Lower $\lambda$ instead.
- **Comparing realised shortfall against $V$ instead of $\sqrt{V}$**: the Almgren-Chriss $V(x)$ is a variance, in currency *squared*. Alerting on $\text{IS} > E + V$ compares incommensurable units and will effectively never fire. Use `stdev` as the scale.
- **Treating the schedule as inviolable**: submitting each child order at its scheduled size even when the book has thinned or the price has gapped. The schedule is a plan; re-evaluate against live liquidity.
- **No catch-up / give-up policy**: leaving the behaviour on rejected or partial fills undefined until it happens live. Decide before trading — catch-up buys impact, give-up buys opportunity cost, and the shortfall report will show which you chose.
- **Predictable child-order patterns**: evenly spaced, identical child orders are detectable and exploitable. Randomise timing and sizing within each interval.
- **Negative slices from independent slice rounding**: on 7 shares over 9 intervals the old rounding-plus-residual approach emitted `[1,1,1,1,1,1,1,1,-1]` — on a plain risk-neutral TWAP, not an exotic parameterisation. A negative slice is an order in the opposite direction.
- **Treating $\kappa$ as $\sqrt{\lambda}$**: drops $\sigma$ and $\eta$, so an illiquid small cap and a liquid mega cap get the same schedule at the same risk aversion. $\kappa$ has units of 1/time and depends on $\lambda\sigma^2/\tilde\eta$.
- **Overflowing on an urgent schedule**: $\sinh(\kappa N)$ raises `OverflowError` past $\kappa N \approx 710$ even though the ratio is bounded by 1. Evaluate the ratio in exponential form.
- **Comparing bucket-by-bucket figures across TCA systems**: some vendors measure opportunity cost from the arrival price rather than $P_0$, which shifts money between the delay and opportunity buckets while leaving the total unchanged. Confirm the boundary before comparing components.
- **Moving the horizon to flatter the report**: opportunity cost is linear in $P_{\text{final}}$. Fix the horizon convention before measuring, not after seeing the number.
- **Rounding to cents before computing basis points**: on a low-priced instrument (many crypto pairs, FX, penny stocks) a genuine 1,000 bps shortfall can be worth a fraction of a cent, so cent-rounding first reports 0.00 bps. Derive bps from the unrounded shortfall and round only the output.
- **Mismatching $\sigma$ and $\tau$ units**: only the product $\kappa\tau$ shapes the schedule, so feeding per-day volatility with $\tau$ in seconds silently rescales urgency instead of raising.

## Verification

- **IS decomposition**: `ImplementationShortfallEngine().evaluate_implementation_shortfall(...)` for a BUY of 10,000 @ $P_0 = \$100.00$, filling 4,000 @ \$100.20 and 4,000 @ \$100.30, 2,000 unfilled at $P_{\text{final}} = \$101.00$, \$20 fees. Expect `execution_cost_usd` $= \$2{,}000$, `opportunity_cost_usd` $= \$2{,}000$, `explicit_fees_usd` $= \$20$, total $= \$4{,}020$ $= 40.20$ bps. Re-run with `arrival_price=100.10`: `delay_cost_usd` $= \$800$, `market_impact_cost_usd` $= \$1{,}200$, summing exactly to the execution cost with the total unchanged.
- **Sell mirror**: the same order as a SELL filling at \$99.80/\$99.70 with $P_{\text{final}} = \$99.00$ must report the identical positive costs and 40.20 bps.
- **$\kappa$ closed form**: `almgren_chriss_kappa(4.0) == math.acosh(3.0)`, and `almgren_chriss_kappa(4.0, 2.0, 8.0) == math.acosh(2.0)` — confirming $\sigma$ and $\eta$ actually enter.
- **Trajectory**: $\lambda = 0$ over 5 intervals on 10,000 shares gives exactly `[2000]*5`; $\lambda = 0.01$ matches Eq. (17) evaluated directly with `math.sinh`; across a sweep of $\lambda$, interval counts and quantities every slice is $\ge 0$ and sums to $Q$; $\lambda = 10^{7}$ returns `[10000, 0, 0, 0, 0]` instead of raising `OverflowError`.
- **Long horizons**: with $\lambda$ chosen so $\kappa = 1$ exactly ($\lambda = 2(\cosh 1 - 1)$), the first interval must hold $1 - e^{-1} \approx 63.2\%$ of the parent at 10, 701, 1,000 and 5,000 intervals alike — the half-life does not depend on $T$. At 698-700 intervals (where an unguarded $\sinh$ product turns NaN) the call must not raise; past 700 it must not collapse the whole parent into interval 0. At $\kappa = 0.5$ the first interval is $1 - e^{-0.5} \approx 39.3\%$ at 1,396-2,000 intervals. A 20,000-interval schedule stays finite, sums exactly, and never rises.
- **Small orders**: 1 share over 10 intervals sums to 1 with every slice in $\{0, 1\}$; 3 shares over 10 intervals sums to 3 with no negative slice; the same inputs always give the same output.
- **Arrival price**: the median of an odd window is the central mid and of an even window the mean of the two central mids; a single wide flicker moves the mean but not the median; a locked book is accepted; an empty window, a crossed quote, a non-positive or non-finite price, and a malformed entry must each raise.
- **Cost forecast**: a uniform schedule reproduces Eqs. (10)/(11); a single-interval dump reproduces Eq. (13) with $V = 0$; the exact optimal trajectory reproduces Eq. (20) to $10^{-10}$ relative at several $(\kappa, N)$. $E$ and $V$ are strictly positive across the usable $\kappa$ range, $V$ rises monotonically as urgency falls and converges to the TWAP variance, front-loading trades expected cost for variance, and `stdev` $= \sqrt{V} \ne V$.
- **Negative checks**: NaN/infinite/zero/negative fill price, non-finite fee, non-positive fill quantity, duplicate `fill_id`, executed quantity exceeding $Q$, invalid `side`, negative $\lambda$, $\tilde\eta \le 0$ (in both `almgren_chriss_kappa` and `ImpactParameters`), an empty or all-zero schedule, a negative child size and a negative `risk_aversion` must each raise.
- Run `python -m unittest discover -s skills/implementation-shortfall-minimization/scripts` — 76 tests, all passing.

## Related Skills

- `execution-slippage-attribution-timing-vs-sizing`
- `post-trade-execution-quality-scorecard`
- `transaction-cost-analysis-tca-integration`
- `execution-algo-twap-vwap-slicing`
- `participation-of-volume-pov-execution`
- `multi-day-execution-schedules-for-very-large-orders`
- `algo-parameter-defaults-by-instrument-liquidity-tier`
- `auction-only-order-types-for-illiquid-names`
- `order-placement-idempotency`
