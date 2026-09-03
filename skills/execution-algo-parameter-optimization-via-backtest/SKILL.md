---
name: execution-algo-parameter-optimization-via-backtest
description: >-
  Use when picking concrete values for the parameters an execution algorithm exposes,
  such as participation ceiling, Almgren-Chriss risk aversion and peg offset, by grid
  search over historical intraday paths scored on implementation shortfall.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: backtesting-methodology, execution-algo, parameter-optimization, almgren-chriss, implementation-shortfall, market-impact, tca
  brokers_frameworks: "Almgren-Chriss Optimal Execution Framework; Almgren-Thum-Hauptmann-Li Market Impact Model; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill in pre-deployment execution research, when a desk must pick concrete values for the parameters an execution algorithm exposes — the participation ceiling $\alpha_{\max}$, the Almgren-Chriss risk aversion $\lambda$ that sets how front-loaded the schedule is, and the peg offset in ticks — and wants those values chosen against replayed historical order flow rather than by convention. Each candidate $\theta_i = (\alpha_{\max}, \lambda, \text{peg})$ is replayed over every historical parent order's actual intraday price and volume path, and scored on Implementation Shortfall measured against the arrival price, *including* the opportunity cost of any quantity the schedule failed to complete.

## When NOT to Use

- **As a substitute for calibrating your own impact model.** The optimizer prices impact with the Almgren, Thum, Hauptmann & Li (2005) coefficients, fitted to Citigroup US large-cap desk flow in 2001-2003 with an $R^2$ under one percent. They estimate the *expectation* of cost; individual orders scatter enormously around it. Recalibrate against your own realized TCA first — see `execution-cost-model-recalibration-cadence`.
- **On a sample too small to separate the candidates.** Implementation Shortfall across real paths has a standard deviation in the tens of basis points, so the standard error on a 40-order mean is several bps — routinely larger than the gap between the top candidates. The engine reports `selection_is_separated` and refuses to present a noise-driven ranking as a result; heed it rather than reading the top row.
- **As a fill simulator.** Fills are a participation-capped schedule, not a matching engine. There is no queue position, no venue routing, no order-book depth, and no lot rounding. See `execution-realistic-simulation`, `queue-position-modeling-for-passive-orders`, and `minimum-fill-size-and-lot-rounding-logic`.
- **To justify trading above your participation policy.** The engine will not select a candidate above `max_allowed_participation_rate`. Raising that limit is a risk decision made by a human, not an optimization output.
- **On a single market regime.** A grid tuned only on a quiet period selects a patient schedule that will be badly wrong in a stressed one. Cover multiple regimes and validate on a holdout — see `multi-year-regime-coverage-requirement` and `walk-forward-optimization-window-management`.

## Prerequisites

- Historical parent orders with: order quantity, **side**, arrival (decision) price, and the observed intraday **market price path** over the execution horizon — one price per interval.
- Per-interval market volume (`interval_volumes`). If omitted, ADV is split uniformly across the horizon, which understates the capacity genuinely available at the open and close.
- `execution_horizon_days` — the fraction of a trading day the path spans. This is the $T$ in the impact model's participation rate $X/(V \cdot T)$; leaving it at the 1.0 default while supplying a one-hour path overstates available liquidity.
- `shares_outstanding` ($\Theta$) if permanent impact is to be included at all. Without it the ATHL permanent term is skipped and total cost is understated — the report says so explicitly.
- A **separate holdout set** of orders for out-of-sample validation.
- Impact coefficients calibrated to your own flow, if you have them.

## Workflow

1. **Construct the parameter grid**:
   - Candidate combinations $\theta_i = (\alpha_{\max}, \lambda, \text{peg\_offset})$.
   - **Decision point — the participation ceiling is a risk limit before it is a search dimension.** Candidates above `max_allowed_participation_rate` are excluded from selection and recorded in `rejected_configs` with the reason. If that empties the grid the call raises rather than returning an arbitrary answer.

2. **Derive the schedule for each candidate**:
   - Almgren & Chriss (2000) Eq. (17) sets the target inventory trajectory:
     $$x_j = X \cdot \frac{\sinh\!\big(\kappa (T - t_j)\big)}{\sinh(\kappa T)}$$
   - $\kappa$ follows AC Eq. (19), $\kappa \sim \sqrt{\lambda \sigma^2 / \eta}$, with $\eta$ linearised from the ATHL temporary-impact cost at the participation ceiling.
   - **Decision point — $\lambda$ is not an additive cost.** It selects *where on the efficient frontier* you trade. Raising $\lambda$ front-loads the schedule: more impact, less exposure to the price path. A model in which $\lambda$ simply adds shortfall is degenerate — the optimizer would always return the smallest $\lambda$ in the grid.
   - $\kappa \to 0$ is the risk-neutral limit, where the trajectory is linear (TWAP).

3. **Replay each order over its historical path**:
   - Per interval, the target slice is capped at $\alpha_{\max} \times$ that interval's observed volume; any shortfall against the target **rolls forward** rather than vanishing.
   - **Decision point — fill completion is an outcome, not an assumption.** If the observed volume could not have absorbed the schedule at the configured ceiling, the order ends partially filled and that is the result, not an error.
   - Each slice prices off the observed market price, displaced by permanent impact accumulated from our own prior fills, plus the ATHL temporary impact at that slice's participation rate, plus the peg concession $\text{peg\_ticks} \times \text{tick\_size}$.

4. **Compute Implementation Shortfall (Perold 1988)**:
   $$\text{IS} = f \cdot \underbrace{s \frac{P_{\text{VWAP}} - P_{\text{arrival}}}{P_{\text{arrival}}} 10^4}_{\text{execution cost on the filled part}} + (1 - f) \cdot \underbrace{s \frac{P_{\text{final}} - P_{\text{arrival}}}{P_{\text{arrival}}} 10^4}_{\text{opportunity cost on the unfilled part}}$$
   with $f$ the fill fraction and $s = +1$ for a buy, $-1$ for a sell.
   - **Decision point — the unexecuted remainder is not free.** Charging only the filled part makes a schedule that quietly gives up on hard orders look like the cheapest one in the grid.

5. **Score and select**:
   $$\text{Score}(\theta) = \overline{\text{IS}} + \gamma_{\text{vol}} \cdot \sigma_{\text{IS}} + (1 - \bar{f}) \cdot w_{\text{fill}}$$
   - Lower is better. $w_{\text{fill}}$ is a *policy* statement in bps per unit of unfilled fraction, not a market-derived quantity.
   - Ties resolve to the earliest candidate in grid order, so the same inputs always select the same configuration.

6. **Check the result is a result**:
   - **Decision point — compare the winner's margin to the standard error.** The report gives `mean_is_standard_error_bps` per candidate and `selection_is_separated` for the winner-versus-runner-up gap. When that flag is false the ranking is sampling noise and the top candidates must be treated as equivalent.
   - **Decision point — read the holdout degradation before promoting anything.** Pass `holdout_samples`; a run without one carries an explicit `NO HOLDOUT SUPPLIED` warning and must not be promoted on that basis alone.

7. **Emit the audit report**: `AlgoOptimizationAuditReport`, carrying every candidate's score, the rejected configurations and why, the holdout comparison, and every warning raised.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A "backtest" that never reads the historical data.** If shortfall is a closed-form function of the parameters alone, the arrival price and the price path are decorative and every candidate is being scored on the model's own assumptions. Test it directly: two samples differing *only* in their price path must produce different shortfalls.
- **Ranking on noise.** With $\sigma_{\text{IS}} \approx 45$ bps and 40 orders, the standard error on the mean is roughly 7 bps. A winner that beats the runner-up by 0.01 bps has not beaten it. This is the single most likely way to misuse the skill.
- **Ignoring the opportunity cost of the unfilled remainder.** A 5% ceiling that completes 40% of a large order can post an excellent shortfall on what it filled while being the worst choice available.
- **Treating $\lambda$ as a cost rather than a position on the efficient frontier.** See the decision point in step 2.
- **Assuming a square-root impact law.** ATHL rejected $\beta = 1/2$ at the 95% confidence level in favour of $\beta = 3/5$ (Sec. 4.2). Square-root understates the cost of large trades and overstates it for small ones.
- **Borrowing impact coefficients as if they were physical constants.** $\gamma = 0.314$ and $\eta = 0.142$ came from one broker's US large-cap flow two decades ago. They are a starting point for calibration, not a result.
- **Overfitting to a quiet in-sample period**, then meeting an unexpected volatility regime with a schedule tuned for calm.
- **Mismatching `execution_horizon_days` to the supplied path.** The participation rate is $X/(V \cdot T)$; a one-hour path left at $T = 1.0$ makes every candidate look cheaper and more fillable than it is.
- **Reading `mean_implementation_shortfall_bps` without `worst_implementation_shortfall_bps`.** A mean can hide one catastrophic order; the report carries both, plus `min_fill_completion_rate`.

## Verification

- Reproduce the published Table 3 of Almgren, Thum, Hauptmann & Li (2005) directly: `athl_permanent_impact_fraction(0.0157, 100_000, 1_000_000, 263_000_000)` must give $\approx 20$ bp and `athl_temporary_impact_fraction(0.0157, p)` must give $\approx 22 / 15 / 8$ bp at participation $1.0 / 0.5 / 0.2$; the realized cost $J = I/2 + K$ must give $\approx 32$ bp.
- Check the trajectory against Almgren & Chriss (2000) Eq. (17): `_ac_inventory_fraction(2.0, 0.5)` must equal $\sinh(1)/\sinh(2) = 0.32403$, the $\kappa \to 0$ limit must be linear, and $\kappa T = 10^4$ must not overflow.
- Check the shortfall arithmetic with impact switched off (`ImpactModelCoefficients(permanent_gamma=0.0, temporary_eta=0.0)`): one tick of $0.01$ on a $100.00$ arrival price is exactly $1.0$ bp; a half-filled order on a path that ends $10\%$ higher is exactly $500$ bps; a sell on the mirrored path gives the identical figure.
- Negative checks: `order_qty=0`, a negative or `NaN` arrival price, `max_participation_rate` of $0$ or $1.5$, a negative peg offset, an `interval_volumes` length mismatch, an unrecognised side, an empty grid, and a grid entirely above the participation limit must each raise.
- Confirm the guards fire: a run without `holdout_samples` must warn, a candidate above the participation ceiling must appear in `rejected_configs`, and a winner inside the sampling noise must set `selection_is_separated` to false.
- Run `python -m unittest discover -s skills/execution-algo-parameter-optimization-via-backtest/scripts` and confirm 100% pass rate.

## Related Skills

- `implementation-shortfall-minimization`
- `transaction-cost-analysis-tca-integration`
- `execution-cost-model-recalibration-cadence`
- `execution-slippage-attribution-timing-vs-sizing`
- `execution-realistic-simulation`
- `backtest-parameter-sensitivity-analysis`
- `walk-forward-validation-setup`
- `algo-parameter-defaults-by-instrument-liquidity-tier`
