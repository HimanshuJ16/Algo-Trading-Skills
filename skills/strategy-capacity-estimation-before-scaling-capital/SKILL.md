---
name: strategy-capacity-estimation-before-scaling-capital
description: >-
  Single-strategy AUM capacity estimator that prices scaling drag with the empirical square-root law of market impact and half-spread friction, decays the net Sharpe ratio across an AUM grid, and reports the largest capital level clearing both a minimum net Sharpe gate and an ADV participation cap.
domain: Portfolio & Risk Management
subdomain: AUM Capacity Estimation & Capital Scaling
tags: ["strategy-capacity", "aum-scaling", "square-root-impact-law", "market-impact", "sharpe-decay", "turnover-limit", "adv-participation"]
brokers_frameworks: ["Square-Root Law of Market Impact", "Portfolio Capacity Frameworks", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deciding how much capital a quantitative strategy can absorb before its own trading destroys its edge. As AUM scales, traded notional scales with it, and execution cost per dollar traded grows with the square root of participation. That drag compounds against a fixed gross alpha, so net return per dollar falls and the realized Sharpe ratio decays. The engine walks an AUM grid, prices half-spread and market-impact drag at each level, and reports the largest AUM that still clears both a minimum net Sharpe gate and a maximum ADV participation cap — along with which of the two bound first.

Reach for it before an allocation increase, when sizing a new strategy's target book, or when a research backtest run at $1M is about to be promoted to institutional size.

## When NOT to Use

- **As an alpha-decay model.** `annual_gross_return_pct` is assumed **invariant to AUM**. Real gross alpha decays with size independently of execution cost — signal capacity exhausts, the book dilutes into worse names, and crowding erodes the edge. Every capacity number this engine produces is therefore an **upper bound**. Treat it as a ceiling to stay under, never a target to reach.
- **As a per-name liquidity check.** The engine compares aggregate portfolio turnover against a single aggregate `avg_daily_volume_usd`. A real book spreads turnover across names whose ADVs differ by orders of magnitude, and capacity binds on the least liquid names long before it binds on the aggregate. Pair with `liquidity-adjusted-position-sizing` and `concentration-risk-single-name-limits`.
- **With an uncalibrated `impact_gamma`.** The square-root prefactor $Y$ is the single largest lever on the result and the default sits at the optimistic end of the empirical range. An unfitted value produces a confident but meaningless capacity figure.
- **As an execution scheduler or a risk control.** Nothing here slices orders, sets a participation schedule, or halts anything. For scheduling see `execution-algo-twap-vwap-slicing` and `multi-day-execution-schedules-for-very-large-orders`; for enforcement see `kill-switch-and-drawdown-circuit-breakers`.
- **For a strategy that never clears its Sharpe gate at any size.** That result comes back as `BELOW_MIN_SHARPE_AT_ALL_SIZES` and is a strategy-quality verdict, not a capacity limit — more liquidity would not relieve it.

## Prerequisites

- Strategy performance parameters (`StrategyParameters`: `strategy_id`, `annual_gross_return_pct`, `annual_volatility_pct`, `daily_turnover_pct`, `avg_daily_volume_usd`, `avg_daily_volatility_pct`, `half_spread_bps`, `max_participation_rate_pct`, `min_acceptable_sharpe`, `risk_free_rate_pct`).
- **Unit contract**: returns, volatilities, and turnover are **fractions** (`0.25` = 25%); `half_spread_bps` is in **basis points**; `max_participation_rate_pct` is a **percentage** (`5.0` = 5% of ADV). `avg_daily_volume_usd` must be in the same currency as AUM.
- `daily_turnover_pct` is **one-way** notional traded per day as a fraction of AUM, paired with a **half**-spread charged once on that notional. If your turnover figure is two-way, halve it before passing it in or you double every cost in the model.
- An `impact_gamma` fitted to your own realized slippage. Empirical values for stocks and futures fall roughly in $0.5 \dots 1.0$; the `0.5` default is the optimistic end.

## Workflow

1. **Fix the unit and horizon contract before anything else**:
   - Non-finite inputs are rejected, not priced. A NaN return propagates to a NaN Sharpe, and because every comparison against NaN is `False` the gate silently passes — the engine would otherwise report a confident capacity for an unpriced strategy.
   - Zero volatility and zero ADV are rejected rather than divided by. Zero volatility is a division by zero, not an infinitely good strategy; zero ADV is an untradeable instrument, not one of unbounded capacity.
   - Decide whether `annual_gross_return_pct` is a total or an excess return. If total, set `risk_free_rate_pct`; leaving it at `0.0` credits the risk-free rate to the strategy and overstates every Sharpe by $r_f/\sigma$.
2. **Daily friction modelling at each AUM level**:
   - Daily one-way notional $Q = \text{AUM} \times \text{turnover}$; participation $= Q / \text{ADV}$.
   - Square-root impact law: $I(Q) = Y \cdot \sigma_{\text{daily}} \cdot \sqrt{Q / V}$ — **not** Almgren-Chriss, which is a *linear*-impact optimal-execution model (see `references/standards.md`).
   - Half-spread cost $= Q \times \text{half\_spread\_bps} / 10^4$, charged once on one-way notional.
   - Both are annualised over 252 trading days. Crypto and FX venues do not follow that convention.
3. **AUM grid construction**:
   - The grid is $\text{step}, 2 \times \text{step}, \dots \le \text{max\_search}$, index-derived rather than accumulated — repeated `+=` on a non-representable step drifts and can drop the final point.
   - A non-positive step and a step wider than the search range are rejected: the first never terminates, the second yields an empty curve whose zero capacity is indistinguishable from a real answer.
4. **Net Sharpe decay**: $\text{Sharpe}_{\text{net}} = (R_{\text{net}} - r_f) / \sigma_{\text{strategy}}$, using **gross** strategy volatility. Costs are modelled as a deterministic drag, so realized impact variance is ignored and net Sharpe is biased upward.
5. **Capacity limit and limiting factor**:
   - Capacity is the largest AUM with an **unbroken feasible run beneath it** — the search stops at the first breach. Taking the last feasible point anywhere on the grid would jump across a breached region.
   - Classify the binding constraint: `ADV_PARTICIPATION_LIMIT`, `MIN_SHARPE_BREACH`, `BELOW_MIN_SHARPE_AT_ALL_SIZES`, or `SEARCH_RANGE_EXHAUSTED`. The last means no gate broke inside the searched range — the answer is censored by the loop bound and is **not** evidence of unlimited capacity. Widen `max_search_aum_usd` and re-run before treating it as anything.
6. **Read the optimum correctly**: `optimal_sharpe_capacity_aum_usd` maximises net dollar PnL **among feasible points only**. Net dollar PnL keeps climbing well past the point where the strategy breaches its own gates, so the unconstrained peak is reported separately as `unconstrained_max_pnl_aum_usd` — a diagnostic, never an allocation target.
7. **Execution output**: structured `StrategyCapacityReport`, carrying `capacity_resolution_usd`, `search_range_exhausted`, and the `impact_gamma` and `risk_free_rate_pct` actually used, so the number is auditable rather than merely reported.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling the square-root law "Almgren-Chriss".** Almgren and Chriss (2000) solve optimal liquidation under *linear* temporary and permanent impact; they do not propose a square-root law. The $\sigma\sqrt{Q/V}$ form is a separate empirical regularity from Torre/BARRA (1997) and Grinold and Kahn (1999). Earlier versions of this skill made exactly this mis-attribution. Citing the wrong paper hides that the exponent is an empirical fit — and one that Almgren et al. (2005) and Kyle and Obizhaeva (2016) put nearer $0.6$ than $0.5$.
- **Shipping the default `impact_gamma`.** Impact drag is linear in $Y$, but capacity is not: where the Sharpe gate binds it scales as $Y^{-2}$, so moving from the $0.5$ default to the top of the measured $0.5\dots1.0$ range cuts estimated capacity roughly **fourfold** — on the reference parameters, from \$133M to \$33M. Running uncalibrated does not produce a rough estimate; it produces a systematically optimistic one, and the direction of the error is always toward over-allocation. (Where the participation cap binds instead, capacity is independent of $Y$ — check the `limiting_factor` before deciding how much the calibration matters.)
- **Reading `SEARCH_RANGE_EXHAUSTED` as unlimited capacity.** It means the loop ended, not that the strategy scales. Scaling to `max_capacity_aum_usd` in that state is scaling to a function argument you chose arbitrarily.
- **Allocating to the unconstrained PnL peak.** With the reference parameters, net dollar PnL is still rising at $100M against a true capacity of $25M. Bigger is more profitable right up until the participation cap makes the fills unachievable, and the dollar-PnL curve gives no warning at that boundary.
- **Treating `max_capacity_aum_usd` as exact.** It is a grid point. True capacity lies within `capacity_resolution_usd` above it, and `0.0` means "below one grid step", not "exactly zero".
- **Dividing total return by volatility and calling it Sharpe.** A Sharpe ratio is an *excess* return per unit of risk (Sharpe 1994). At a 4% risk-free rate against 15% volatility, omitting $r_f$ adds $+0.27$ — enough on its own to carry a strategy over a 1.0 gate it does not clear.
- **Believing the 5% ADV cap is a rule.** It is a practitioner risk convention with no general regulatory backing. The US ADV-anchored limit that does exist — SEC Rule 10b-18's 25% ADTV volume condition — is a non-exclusive safe harbour for *issuer repurchases* and does not apply here. See `references/standards.md`.
- **Applying an aggregate ADV to a diversified book.** Comparing total portfolio turnover against a single blended ADV hides the illiquid tail, which is where capacity actually binds first.
- **Projecting a frictionless backtest Sharpe onto institutional AUM.** A strategy that shows 1.67 at $1M is not a 1.67 strategy at $100M, and the gap is not a rounding error.

## Verification

- Instantiate `StrategyCapacityEstimatorEngine(impact_gamma=0.5)` with a 25% gross / 15% vol strategy, 10% one-way daily turnover, $50M ADV, 1.5% daily volatility, 1 bp half-spread, 5% participation cap. Verify frictionless Sharpe $1.67$ at $r_f = 0$.
- At AUM $\$25\text{M}$, verify against the hand derivation: participation exactly $5.00\%$, spread cost $\$63{,}000$, impact cost $\$1{,}056{,}542.12$, net PnL $\$5{,}130{,}457.88$, net Sharpe $1.3681221015$.
- Verify the participation cap is **inclusive**: $\$25\text{M}$ is feasible and $\$26\text{M}$ is not, giving `max_capacity_aum_usd` $= \$25\text{M}$ and `limiting_factor` $=$ `ADV_PARTICIPATION_LIMIT`.
- Verify `optimal_sharpe_capacity_aum_usd` $\le$ `max_capacity_aum_usd`, while `unconstrained_max_pnl_aum_usd` reaches the $\$100\text{M}$ search ceiling — the feasible optimum must not follow the dollar-PnL curve past the cap.
- Set $r_f = 0.04$ and verify frictionless Sharpe drops to exactly $1.40$, and that every curve point drops by $0.04/0.15$.
- Verify impact obeys the square-root law: quadrupling traded notional exactly **doubles** impact, and doubling `impact_gamma` exactly doubles it.
- Verify a search that breaches nothing returns `SEARCH_RANGE_EXHAUSTED` with `search_range_exhausted=True`, never `UNLIMITED`.
- Verify NaN/Inf inputs, zero volatility, zero or negative ADV, negative turnover, a non-positive `aum_step_usd`, and a step wider than the search range all raise rather than returning a report.
- Run `python -m unittest discover -s skills/strategy-capacity-estimation-before-scaling-capital/scripts`.

## Related Skills

- `portfolio-construction-with-transaction-cost-awareness`
- `incremental-capital-deployment-for-new-strategies`
- `liquidity-adjusted-position-sizing`
- `execution-cost-model-recalibration-cadence`
- `capital-reallocation-based-on-live-performance`
