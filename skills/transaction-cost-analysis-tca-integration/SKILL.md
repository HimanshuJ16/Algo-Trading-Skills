---
name: transaction-cost-analysis-tca-integration
description: >-
  Use when validating strategy backtests to integrate Transaction Cost Analysis
  (TCA), decompose implementation shortfall into delay cost, spread cross,
  square-root market impact, commissions, and opportunity cost on unfilled
  shares, compare modelled cost against realized fills, and calibrate the
  backtest slippage model from the difference.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- tca-integration
- implementation-shortfall
- market-impact
- slippage-calibration
- transaction-costs
brokers_frameworks:
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when validating strategy profitability during backtesting. Naive backtests assume zero slippage or a flat fee, producing Sharpe ratios that collapse in live trading. This engine produces **two independent numbers per trade and their difference**:

- an **estimated** (ex-ante) shortfall from a cost model — delay + half-spread + $\gamma\sqrt{\text{Size}/\text{ADV}}$ + commission;
- a **realized** (ex-post) shortfall measured from the actual fill against the decision price, $IS = (P_{\text{fill}} - P_{\text{decision}})/P_{\text{decision}}$ (Perold 1988);
- `model_error_bps` = realized − estimated, which is the only quantity that can actually **calibrate** a slippage model.

A backtest that only knows the estimate never learns it is wrong. A TCA report that only knows the realization cannot tell you which cost component to fix.

## When NOT to Use

- **You have no fills yet.** Realized shortfall and calibration need `p_fill` from real or paper executions. Pre-trade sizing alone is `liquidity-adjusted-position-sizing`.
- **You need an execution schedule.** Each trade is one fill at one price. No slicing, no participation trajectory, no impact decay — see `execution-algo-twap-vwap-slicing`.
- **Your `gamma` is uncalibrated.** The impact term is meaningless until fitted to your own fills. Run `suggest_market_impact_gamma` first; the default is a placeholder, not a constant.
- **You need Sharpe or drawdown.** This engine returns a return *drag*, not a risk-adjusted performance series.

## Prerequisites

- Per-order records: decision timestamp and price $P_{\text{decision}}$, arrival price $P_{\text{arrival}}$, VWAP fill price $P_{\text{fill}}$, quoted spread at arrival, order size, and **filled size** (defaults to a complete fill).
- Average Daily Volume (ADV) in the same units as order size, strictly positive.
- The **capital base** that produced the gross backtest return, in the same currency as the prices. `evaluate_portfolio_tca` requires it.
- Optional terminal benchmark price $P_{\text{end}}$ for pricing opportunity cost on any unfilled remainder.

## Workflow

1. **Measure realized shortfall before modelling anything**:
   $$IS_{\text{realized}} = d \cdot \frac{P_{\text{fill}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10^4 + \text{commission}_{\text{bps}}, \quad d = +1 \text{ (buy)}, -1 \text{ (sell)}$$
   The direction term is not cosmetic: without it every sell's cost carries the wrong sign. Costs are positive-is-adverse on both sides.

2. **Decompose the modelled estimate** into components that can be attributed and fixed independently:
   - **Delay cost**: $d \cdot (P_{\text{arrival}} - P_{\text{decision}})/P_{\text{decision}} \times 10^4$ — signal-to-venue latency.
   - **Half-spread cross**: $0.5 \cdot \text{Spread}/P_{\text{decision}} \times 10^4$ — charged unconditionally, so it **over-charges passive fills**. For maker flow read the realized number instead.
   - **Market impact**: $\gamma\sqrt{\text{Size}/\text{ADV}}$.
   - **Commissions and fees**: only the part the broker does not already fold into the fill price.

3. **Check the participation rate before trusting the impact term.** If $\text{Size}/\text{ADV}$ falls outside $[10^{-5}, 0.1]$ the engine sets `participation_out_of_model_range` and logs a warning: impact crosses over toward linear below that band, and published fits are calibrated on metaorders small relative to ADV, conventionally taken as up to 10% participation. The number is still computed — it is an extrapolation, not a clamp — and must be treated as an unreliable estimate rather than silently trusted.

4. **Price the unfilled remainder or declare it unpriced.** Perold's IS covers the whole order, not just the filled part. With fill ratio $f$:
   $$IS_{\text{total}} = f \cdot IS_{\text{exec}} + (1-f)\cdot d\frac{P_{\text{end}} - P_{\text{decision}}}{P_{\text{decision}}}\times 10^4 + f \cdot \text{commission}_{\text{bps}}$$
   If shares went unfilled and no $P_{\text{end}}$ was supplied, `opportunity_cost_bps` is `None`, **never** `0.0`, and `total_implementation_shortfall_bps` is `None`. Do not substitute zero: the orders that failed to fill are usually the expensive ones.

5. **Calibrate the slippage model from the residual, not from a guess.** `suggest_market_impact_gamma` strips delay and half-spread from realized cost and refits by least squares:
   $$\hat{\gamma} = \frac{\sum_i r_i \sqrt{\phi_i}}{\sum_i \phi_i}, \quad r_i = IS_{\text{exec},i} - \text{delay}_i - \text{spread}_i, \quad \phi_i = \text{Size}_i/\text{ADV}_i$$
   A **negative** fit is clamped to `0.0` with a warning — impact cannot be a credit, and a negative residual means something other than impact (passive fills earning the spread, or favourable drift) dominates. Refit per instrument liquidity bucket and per volatility regime, not once globally.

6. **Convert to a return drag through the capital base, never through the trade count.**
   $$\text{drag}\% = \frac{\sum_i \text{cost}_i^{\text{currency}}}{\text{capital base}} \times 100, \quad \text{cost}_i^{\text{currency}} = \frac{IS_i}{10^4}\cdot(\text{filled}_i \cdot P_{\text{decision},i})$$
   Judge viability on `notional_weighted_shortfall_bps`, not on the equal-weighted mean — the equal-weighted figure lets a thousand odd-lot trades outvote the one block that actually cost money. Check `unpriced_opportunity_trades`: if non-zero, `net_tca_return_pct` is an optimistic bound.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading the modelled estimate as if it were measured cost.** `estimated_shortfall_bps` never touches `p_fill`; a catastrophic fill and a perfect one produce the identical estimate. Only `realized_shortfall_bps` knows what execution actually cost.
- **Adding the realized and modelled numbers together.** Realized shortfall already subsumes delay, spread and impact as they actually occurred. Summing them double-counts every component; they are meant to be *differenced*.
- **Turning a per-trade bps cost into a portfolio return by multiplying by trade count.** Drag is currency cost over capital. A thousand one-share trades cost cents, not thirty-five percentage points; a single half-ADV block can cost more than all of them combined.
- **Treating `gamma` as a portable constant.** The canonical law is $I = Y\sigma\sqrt{Q/V}$ with $\sigma$ the daily volatility (Tóth et al. 2011). Folding $\sigma$ into a bps constant makes $\gamma$ specific to one instrument in one volatility regime — a $\gamma$ fitted on a 20%-vol large cap badly under-prices a 120%-vol microcap and over-prices the large cap once volatility mean-reverts.
- **Believing the square-root exponent is settled.** Almgren et al. (2005) reject $1/2$ for temporary impact in favour of $3/5$; published fits span roughly 0.4–0.7. The square-root form is a baseline, not a law of nature.
- **Substituting zero for an unpriced opportunity cost.** A missed fill in a market that ran away from you is the single most expensive outcome in the IS framework. Reporting it as free inverts the ranking of your execution venues.
- **Defaulting ADV to 1 when the data is missing.** Any floor turns absent liquidity data into a fabricated participation rate. Reject the record instead; this engine raises `ValueError` on non-positive ADV.
- **Silently capping participation at 100% of ADV.** It makes a 100×-ADV order price identically to a 1×-ADV order — precisely the size where the cost estimate matters most.
- **Charging the half-spread to passive fills.** The estimate assumes every fill takes liquidity. A resting order that earns the spread is over-charged by the model and correctly priced only by the realized figure.
- **Omitting the side sign.** For a sell, a price *fall* between decision and fill is adverse. Without the direction term, profitable sells book as costs and vice versa.
- **Double counting fees.** Adding `fixed_commission_bps` on top of a broker fill price that already nets exchange and regulatory fees charges them twice.

## Verification

- **Decomposition against hand arithmetic.** BUY 10,000 units, ADV 100,000, $P_{\text{decision}}=150.00$, $P_{\text{arrival}}=150.02$, $P_{\text{fill}}=150.10$, spread $0.04$, $\gamma=15$, commission $2.5$ bps. Verify delay $=4/3$ bps, half-spread $=4/3$ bps, impact $=15\sqrt{0.10}=4.743416$ bps, estimated total $=9.910083$ bps, realized $=20/3+2.5=9.166667$ bps, and `model_error_bps` $=-0.743416$ (the model over-predicted). Verify currency cost $=1{,}375.00$ on 1,500,000 notional.
- **`p_fill` is actually read.** Re-run with $P_{\text{fill}}=300.00$ and verify `realized_execution_cost_bps` $=10{,}000$ while `estimated_shortfall_bps` is unchanged.
- **Side symmetry.** SELL at $P_{\text{decision}}=100$, $P_{\text{arrival}}=99.90$, $P_{\text{fill}}=99.80$ must give delay $=+10$ bps and realized $=+20$ bps, both positive.
- **Square-root scaling.** Quadrupling participation must exactly double the impact estimate.
- **No silent clamp.** A 4×-ADV order must price at $15\sqrt{4}=30$ bps and set `participation_out_of_model_range`, not sit at $\gamma=15$.
- **Notional-based drag.** 1,000 one-unit trades at 100.00 with 1 bp commission cost 10.00 in total; against a 1,000,000 capital base that is a 0.001% drag, not 10 percentage points.
- **Weighting divergence.** A 100-bps trade on 10,000 notional plus a 10-bps trade on 1,000,000 notional gives an equal-weighted 55 bps but a notional-weighted 10.89 bps; viability is judged on the latter.
- **Opportunity cost.** BUY 1,000 with 400 filled at 100.00 and $P_{\text{end}}=110.00$ gives `opportunity_cost_bps` $=1{,}000$, `total_implementation_shortfall_bps` $=600$, and 6,000 in currency. Omit $P_{\text{end}}$ and both must be `None`, with `unpriced_opportunity_trades` incremented.
- **Calibration recovers a known coefficient.** Fills constructed with residual $=20\sqrt{\phi}$ must refit to $\hat{\gamma}=20.0$, and delay and spread must be stripped before fitting.
- **Invalid input fails loudly.** `adv=0`, negative ADV, `p_decision=0`, negative size, NaN or infinite prices, `action="SEL"`, `filled_size > order_size`, and non-positive `capital_base` must all raise rather than return a plausible number.
- Run `python -m unittest discover -s skills/transaction-cost-analysis-tca-integration/scripts` and confirm 100% pass rate.

## Related Skills

- `execution-realistic-simulation`
- `execution-cost-model-recalibration-cadence`
- `execution-slippage-attribution-timing-vs-sizing`
- `implementation-shortfall-minimization`
- `portfolio-construction-with-transaction-cost-awareness`
- `post-only-and-maker-taker-fee-optimization`
- `vectorized-vs-event-driven-backtest-tradeoffs`
