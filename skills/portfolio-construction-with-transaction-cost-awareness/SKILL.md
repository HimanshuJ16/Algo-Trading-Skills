---
name: portfolio-construction-with-transaction-cost-awareness
description: >-
  Turnover-aware rebalance planner that filters proposed weight changes through a no-trade buffer band and prices the surviving trades with proportional commission, bid-ask spread, and quadratic market impact costs.
domain: Portfolio Multi Strategy
subdomain: Rebalance Cost Accounting & Turnover Management
tags: ["portfolio-construction", "transaction-costs", "rebalancing", "no-trade-band", "turnover-control", "market-impact", "cost-accounting"]
brokers_frameworks: ["Python Dataclasses"]
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when you already have target portfolio weights from an upstream allocator and need to decide **which of those weight changes are worth executing**. Frictionless allocators re-solve every period and emit a stream of tiny weight adjustments; executing all of them burns commission, spread, and market impact that can exceed the alpha being chased. This engine applies a no-trade buffer band to suppress micro-rebalances, prices the surviving trades, and reports gross return, total cost, net return, turnover, and the executable final weight vector.

**When NOT to use it:**

- **You need an optimizer.** This engine does *no* optimization. There is no covariance matrix, no risk-aversion parameter, no mean-variance objective, and no quadratic program — nothing here searches over weight vectors. `target_weight` is an **input**. If you want weights that are optimal net of costs, solve that upstream (e.g. with CVXPY) and feed the solution in.
- **You need an execution schedule.** Costs are charged as if each trade fills at once. For order slicing and participation-rate control see `execution-algo-twap-vwap-slicing`.
- **You need share-level orders.** This engine works in weights only: no lot sizing, tick rounding, or minimum notional.
- **Your impact coefficient is uncalibrated.** The quadratic impact term dominates the cost estimate at realistic trade sizes. An unfitted `impact_coeff` produces a confident but meaningless net return.

## Prerequisites

- Asset alpha specs (`symbol`, `expected_return`, `current_weight`, `target_weight`). Weights are **fractions**, not percentages (`0.40` = 40%); expected returns must be stated over the **same horizon** as the rebalance cost.
- Transaction cost specs (`commission_rate` as a decimal fraction, `spread_cost_bps` in basis points — note the deliberately inconsistent but API-stable units, `impact_coeff` calibrated to your own realized slippage).
- Optimizer config (`rebalance_threshold`: float = 0.02, `max_turnover_limit`: float = 0.50 two-way, `trade_to_band_edge`: bool = False).

## Workflow

1. **Validate inputs before pricing anything**:
   - Reject empty asset lists, duplicate symbols, and non-finite weights or returns. A NaN weight otherwise propagates to a NaN `net_expected_return`, which compares `False` against every risk threshold and silently passes an unpriced rebalance.
   - Reject any $|w| > 10$. Passing `40` for 40% instead of `0.40` inflates the quadratic impact term by four orders of magnitude, so this must fail loudly rather than return a plausible-looking, wildly mispriced plan.
2. **No-Trade Buffer Band Filtering**:
   - Compute proposed change $\Delta w_i = w_{\text{target},i} - w_{\text{current},i}$.
   - If $|\Delta w_i| \le \text{threshold}$, suppress the trade ($w_{\text{final},i} = w_{\text{current},i}$). The band is **inclusive**, and the comparison must be float-tolerant: $0.18 - 0.20$ evaluates to $-0.020000000000000018$, so a naive `<=` sends an exactly-at-threshold move to the trading path and incurs precisely the cost the band exists to suppress.
   - Else choose the band policy. With `trade_to_band_edge=False` (default) snap fully to target. With `trade_to_band_edge=True` move only to the nearest band boundary, $w_{\text{current},i} \pm \text{threshold}$ — under purely proportional costs the optimal policy is to trade back to the boundary, not to the target (Constantinides 1986; Davis and Norman 1990).
3. **Transaction Cost & Market Impact Calculation**:
   - Price the **executed** delta, not the proposed one. Under band-edge mode these differ, and charging the proposed delta overstates cost on every banded trade.
     $$\text{TC}_i = \left(c_{\text{commission}} + \frac{c_{\text{spread,bps}}}{10^4}\right)|\Delta w_i| + c_{\text{impact}}(\Delta w_i)^2$$
   - Costs are fractions of total portfolio value, the same unit as the weights, so they subtract directly from the weighted expected return.
4. **Turnover Audit**:
   - Compute **two-way** turnover $\sum |\Delta w_{\text{executed},i}|$ (the L1 norm) and report one-way turnover as half of it. Compare the two-way figure against `max_turnover_limit`. Conflating the two conventions silently doubles or halves the effective limit.
   - The limit is **advisory**: the plan is flagged via `turnover_limit_breached` but returned **unclamped**, because the engine cannot know which trades the caller would rather drop. Gate execution on this flag yourself.
5. **Budget / Self-Financing Audit**:
   - Suppressing some trades while executing others breaks the budget identity, so the final weight vector generally sums to neither the current nor the target sum. Report `net_weight_change` and `is_self_financing`; a non-zero net change is real money that must come from or go to cash.
6. **Net Expected Utility Evaluation**:
   - Net Return $= \sum (w_{\text{final},i} \cdot \mu_i) - \text{TotalTC}$, using **final** weights — crediting target weights for a suppressed asset books alpha on a trade that never happened.
7. **Audit Report Generation**: Output structured `TCAwarePortfolioReport`, including `final_weights` and per-asset `trade_decisions`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mistaking this for an optimizer.** The name says "portfolio construction", but the engine only filters and prices a weight change that someone else chose. It cannot improve a bad target vector; it will faithfully price a bad one.
- **Treating quadratic impact as empirically correct.** A cost quadratic in trade size corresponds to *linear* price impact and is chosen for tractability (Gârleanu and Pedersen 2013). Measured metaorder impact is **concave** — the square-root law, with fitted exponents around 0.4–0.7 (Almgren et al. 2005; Kyle and Obizhaeva 2016). The quadratic model therefore understates small-trade cost and overstates large-trade cost. Calibrate `impact_coeff` to your own realized slippage over your own typical trade sizes; there is no transferable default.
- **Trading all the way to target on a band breach.** Under proportional costs the optimal correction is to the nearest band boundary. Snapping to target pays proportional cost on weight change the theory says buys no utility, and leaves the position more likely to breach the band again next period.
- **Executing a plan that is not self-financing.** If AAPL's +1% is suppressed while MSFT's +10% executes, the book grows by 10% of portfolio value. That funding leg is not optional; check `is_self_financing` before routing.
- **Comparing an annual alpha to a one-off rebalance cost.** `expected_return` is taken on faith and never horizon-checked. Charging a single rebalance's cost against a full year of expected return makes almost any turnover look profitable.
- **Mixing turnover conventions.** A "50% turnover limit" means one thing as an L1 sum and another as the half-sum used in fund disclosure. This engine limits on the two-way figure.
- **Reading `ENGINE_DISABLED` as a no-trade decision.** A disabled engine produced no plan at all; it did not conclude that trading was uneconomic.
- **Chasing micro signals.** Rebalancing for a 0.1% weight shift that incurs 0.05% in cost consumes all of the alpha.

## Verification

- Instantiate `PortfolioConstructionEngine`. Input 2 assets (`AAPL` current $40\%$ vs target $41\%$, `MSFT` current $30\%$ vs target $40\%$) with buffer threshold $2\%$. Verify `AAPL` is suppressed (inside the no-trade band) and `MSFT` trades a $10\%$ weight shift, giving two-way turnover $0.10$ and one-way $0.05$. Verify `is_self_financing` is `False` with `net_weight_change` $= +0.10$ — the remaining weight is cash, and the MSFT buy must be funded from it.
- With zero impact and a 10 bps proportional rate, verify a single $10\%$ trade costs exactly $0.0010 \times 0.10 = 0.00010$ (1 bp of portfolio value).
- Verify the impact term is quadratic, not linear: doubling the trade size must **quadruple** the impact cost.
- With `trade_to_band_edge=True`, verify a proposed $0.30 \to 0.40$ move lands at $0.32$, not $0.40$, and is charged on the executed $0.02$ delta.
- Verify the boundary is representation-safe: a $0.20 \to 0.18$ move against a $2\%$ band must be **suppressed**, even though the raw float subtraction ($-0.020000000000000018$) exceeds the threshold. A $0.20 \to 0.1799$ move must still trade.
- Verify a NaN weight raises `ValueError` rather than propagating to a NaN net return, and that a weight of `40.0` (percent-vs-fraction error) is rejected.
- Run `python scripts/test_portfolio_construction_with_transaction_cost_awareness.py`.

## Related Skills

- `rebalancing-frequency-optimization-cost-vs-drift`
- `execution-cost-model-recalibration-cadence`
- `transaction-cost-analysis-tca-integration`
- `liquidity-adjusted-position-sizing`
---
