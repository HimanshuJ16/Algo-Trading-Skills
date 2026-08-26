---
name: multi-day-execution-schedules-for-very-large-orders
description: >-
  Use when a parent order is too large to complete in one session: builds a participation-capped multi-session schedule (equal, front- or back-loaded) and prices the horizon tradeoff with Almgren-Thum-Hauptmann-Li (2005) market impact against Almgren-Chriss (2000) overnight timing risk.
domain: Execution Algorithms
subdomain: Multi-Day Order Scheduling & Market Impact Optimization
tags: ["multi-day-execution", "adv-participation-cap", "parent-order", "market-impact", "overnight-risk", "athl-2005", "almgren-chriss"]
brokers_frameworks: ["Almgren-Thum-Hauptmann-Li (2005) Impact Model", "Almgren-Chriss (2000) Timing Risk", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a parent order $Q$ is large enough relative to Average Daily Volume ($\text{ADV}$) that no acceptable participation rate completes it in a single session — typically above roughly $10\%$ of $\text{ADV}$, and unambiguously so at $50\%$–$200\%$. Spreading the order across $N$ sessions lowers the participation rate in each one, which lowers temporary market impact, and raises the inventory held overnight, which raises timing risk. The engine prices both sides of that tradeoff in basis points of the parent notional so the horizon can be chosen on evidence rather than by feel.

## When NOT to Use

- **When the order fits inside one session's participation cap.** Then this is an intraday scheduling problem — use `execution-algo-twap-vwap-slicing` or `participation-of-volume-pov-execution`.
- **As an order router.** The output is a per-session share budget, not orders. Something else has to work each day's budget in the market.
- **As a closed-loop controller.** The schedule is planned once from the inputs supplied. It does not observe fills, react to a halt (`execution-algo-behavior-under-halted-instrument`), or respond to a volatility regime shift (`adaptive-execution-under-volatility-spikes`). Re-run it each session with the actual remaining quantity.
- **When you want the Almgren-Chriss optimal trajectory.** The three profiles here are heuristics under a hard cap, not the closed-form $\sinh$ solution. For that, and for a $\lambda$ that resolves the tradeoff into a single objective, use `implementation-shortfall-minimization`.
- **When the binding constraint is a statutory volume limit rather than impact.** A 10b-18 buy-back or a Rule 144 resale has a cap defined by rule, with a prescribed ADV window — encode that cap and window explicitly (see `references/standards.md`); do not substitute a house limit.
- **When the horizon runs to years.** An order needing more than `MAX_HORIZON_SESSIONS` (2,520) sessions is a capacity question (`strategy-capacity-estimation-before-scaling-capital`), and the engine raises rather than scheduling it.

## Prerequisites

- Parent order in **shares**: `symbol`, `total_parent_quantity`, `current_price`.
- `adv_shares`, with the measurement window recorded alongside the order.
- `volatility_daily_pct` — **daily**, on the same frequency as ADV.
- `max_daily_participation_pct` — a deliberate house limit, or a statutory cap where one applies.
- `shares_outstanding` if permanent impact is wanted; without it that term is reported as `None`, not as zero.
- Optionally `target_horizon_days` and `schedule_profile` (`EQUAL_DAILY`, `FRONT_LOADED`, `BACK_LOADED`).

## Workflow

1. **Daily cap and minimum feasible horizon**:
   $$\text{Cap} = \text{ADV}\times p_{\max},\qquad N_{\min} = \left\lceil \frac{Q}{\text{Cap}} \right\rceil$$
   - **Decision point — $N_{\min}$ is a floor, not a plan.** At $N_{\min}$ the order consumes essentially all available capacity, so the cap fixes every slice and `EQUAL_DAILY`, `FRONT_LOADED` and `BACK_LOADED` all return the same flat schedule. A trajectory shape only exists once `target_horizon_days` exceeds the minimum.
   - A requested horizon below $N_{\min}$ raises. Do not resolve that by widening the cap unless the cap genuinely deserves to be wider.

2. **Trajectory allocation by water-filling**: solve for $\lambda$ with $q_d = \min(\text{Cap}, \lambda w_d)$ and $\sum_d q_d = Q$. Sessions at the cap freeze; the remainder re-shares among the rest in weight proportion.
   - **Decision point — never redistribute clipped excess in index order.** It moves quantity to whichever session comes first and can invert the requested trajectory (a back-loaded schedule that rises, dips, then rises again). Water-filling preserves the shape.
   - The slices must sum to $Q$ exactly; an unbalanced schedule silently over- or under-executes the parent and the engine refuses to emit one.

3. **Impact costing** (Almgren, Thum, Hauptmann & Li 2005):
   - Temporary, per session: $K_d = \eta\,\sigma\,(q_d/V)^{\beta}$ with $\eta = 0.142$, $\beta = 0.600$. Costed per session because it depends on how fast *that* session trades — which is exactly what the profile changes. $\beta < 1$ makes it convex in the rate, so a uniform schedule is the cheapest of the three at a given horizon.
   - Permanent: $I = \gamma\,\sigma\,(Q/V)(\Theta/V)^{1/4}$ with $\gamma = 0.314$; a completed programme bears $I/2$ (Almgren & Chriss 2000, Eq. 8).
   - **Decision point — permanent impact does not fall when you stretch the horizon.** It is a function of total size alone. Only the temporary term responds to the schedule.

4. **Overnight risk audit** (Almgren & Chriss 2000, Eq. 5): $\text{Risk}_{1\sigma} = \sigma_{\text{daily}} P \sqrt{\sum_d x_d^2}$ over the inventory $x_d$ carried out of each session.
   - **Decision point — this is one standard deviation**, on an assumption of independent, zero-drift daily returns. It is not a maximum and not a VaR number; do not compare it to a 99% limit without rescaling.

5. **Horizon selection**: run several candidate horizons and read impact against risk. Record the choice and its rationale with the order.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a participation cap as a rule.** No regulator caps ordinary institutional orders at $10\%$ or $15\%$ of ADV; those are house limits. The caps that *are* binding — 25% of ADTV under 17 CFR § 240.10b-18(b)(4), 25% of ADV under Commission Delegated Regulation (EU) 2016/1052 Art. 3(3), the Rule 144(e) volume test — attach to buy-backs and affiliate resales, each with its own prescribed ADV window. Applying one to an unrelated order treats a safe harbour as a permission.
- **An impact model with no volatility in it.** A coefficient applied to participation alone returns the same cost for a utility and a biotech. Impact scales with $\sigma$; ATHL also reject the square-root exponent $\beta = 1/2$ in favour of $3/5$ at the 95% level, so an implementation using $\sqrt{\cdot}$ contradicts the paper it cites.
- **Costing the schedule off the average daily slice.** Averaging first makes the estimate identical for a flat schedule and one whose slices differ by 40%, which destroys the only number that distinguishes the profiles. Cost each session at its own participation rate, then aggregate quantity-weighted.
- **Expecting a longer horizon to reduce permanent impact.** Under linear permanent impact it depends only on total size. A model that shows it shrinking with the horizon is mis-specified.
- **Forcing single-session execution.** Pushing $100\%$ of ADV through one session pays a temporary-impact rate several times that of a paced schedule and signals the full order to anyone watching the tape.
- **Stretching the horizon to minimise impact.** Ten sessions of residual inventory is ten overnight gaps: earnings, guidance, index reviews, macro prints. Timing risk grows as $\sqrt{\sum x_d^2}$ and quickly dominates the impact saved.
- **A silent fallback on an unrecognised profile.** `"FRONT-LOADED"` is not `"FRONT_LOADED"`; returning a flat schedule for a typo gives the caller a trajectory they did not ask for with no signal. It raises here.
- **Reading session indices as calendar days.** The horizon is in trading sessions with no exchange calendar attached. Holidays shift the completion date, and half-days carry a fraction of normal volume — so the cap for those sessions must be scaled down too.
- **Rolling a shortfall forward without re-planning.** Carrying an unfilled remainder into the next session raises that session's participation above the cap. Re-run the schedule on the actual remaining quantity instead.
- **Trusting the coefficients as universal.** The ATHL fit is Citigroup US large-cap desk flow, 2001–2003, with an $R^2$ under one percent. It predicts an expectation, not an outcome; recalibrate before relying on the level.

## Verification

- Instantiate `MultiDayExecutionSchedulerEngine`. Schedule a 500,000-share `AAPL` order at $\$150$ against 1,000,000 ADV with a $10\%$ cap, $\sigma_{\text{daily}} = 2\%$ and $\Theta = 200{,}000{,}000$ shares outstanding. Verify $N_{\min} = 5$, a cap of $100{,}000$ shares/session, five equal $100{,}000$-share slices, temporary impact $7.1338$ bps ($= 0.142 \times 0.02 \times 0.10^{0.6} \times 10^4$), permanent impact $59.0415$ bps ($= \tfrac{1}{2}\times 0.314 \times 0.02 \times 0.5 \times 200^{1/4}\times 10^4$), and overnight risk $\$1{,}643{,}167.67$ ($219.089$ bps).
- Re-run at `target_horizon_days=20`: temporary impact falls to $3.1051$ bps and overnight risk rises to $\$3{,}727{,}432.09$ ($496.9909$ bps), while permanent impact stays at $59.0415$ bps. That invariance is the check that the permanent term is modelled correctly.
- Regression check: a back-loaded 1,950,000-share order at a 100,000-share cap must return a monotonically non-decreasing 20-session schedule. Clip-and-refill allocation returns `[100k × 12, 66_249.61, 83_750.39, 100k × 6]` instead.
- Negative checks: a participation cap above $1.0$, an unknown or mis-punctuated profile, a fractional or infeasible `target_horizon_days`, a non-finite or non-positive quantity, price or ADV, a negative volatility, and `shares_outstanding` below ADV must each raise.
- Run `python -m unittest discover -s skills/multi-day-execution-schedules-for-very-large-orders/scripts`.

## Related Skills

- `implementation-shortfall-minimization`
- `execution-algo-twap-vwap-slicing`
- `participation-of-volume-pov-execution`
- `strategy-capacity-estimation-before-scaling-capital`
- `global-exchange-holiday-calendar-handling`
- `minimum-fill-size-and-lot-rounding-logic`
- `execution-algo-behavior-under-halted-instrument`
- `iceberg-order-native-broker-support-vs-simulation`
- `execution-slippage-attribution-timing-vs-sizing`
