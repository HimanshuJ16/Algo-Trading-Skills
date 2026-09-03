---
name: tail-risk-hedging-with-options
description: >-
  Use when sizing a systematic out-of-the-money index put overlay against a
  stated annual premium budget. Prices the put with Black-Scholes (including
  dividend yield), returns Delta, Gamma, Vega and Theta, spreads the annual carry
  budget across the roll cycle instead of spending it on every tranche, caps
  hedged notional at portfolio notional, and reports crash payoffs net of premium
  paid. Requires the selected strike's own implied volatility, not ATM vol.
domain: risk-management
subdomain: tail-risk
tags: [options-hedging, tail-risk, otm-puts, black-scholes, convex-payoff, carry-budget, volatility-skew, roll-cycle]
brokers_frameworks: ["Python Standard Library (math)", "Python Dataclasses", "Black-Scholes-Merton", "CBOE SPX Index Options", "OCC Equity Options"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

# Tail Risk Hedging With Options

Sizes a systematic out-of-the-money (OTM) index put overlay: how many contracts a
portfolio can buy per roll cycle without breaching a stated **annual** premium
budget, what Greeks that position carries, and what it is worth in a crash **after**
the premium is deducted.

This is a sizing and budgeting tool. It does not calibrate a volatility surface,
route orders, or manage the position after purchase.

## When to Use

- When converting a policy-level tail-hedge budget ("no more than 2% of AUM a year
  on protection") into a concrete contract count for a specific strike and expiry.
- When you need the honest annual drag of a rolling put program, not the cost of a
  single tranche — the two differ by the number of rolls per year.
- When you need Delta, Gamma, Vega and Theta for an OTM put overlay to feed a
  portfolio-level Greeks aggregation.
- When stress-testing what an overlay actually returns at -10%, -20%, -30% and -40%
  index shocks, net of what it cost.

## When NOT to Use

- **As a positive-expectancy strategy.** Passive OTM index put buying has produced
  negative returns in every decade for which index option data exists, robust to
  maturity and moneyness, because implied volatility and implied negative skewness
  systematically exceed subsequent realisations (AQR 2020, pp.3–5). The peer group
  is not better: the CBOE Eurekahedge Tail Risk Index has returned roughly -2% a
  year since its 2008 inception and about -8% a year through the 2010s. This skill
  sizes that cost deliberately; it does not remove it. If the goal is drawdown
  mitigation rather than a contractual protection floor, compare against trend
  following and other indirect hedges before committing premium.
- **With ATM volatility as the input.** The premium of a 15% OTM put is dominated
  by skew. Passing ATM vol under-prices it several-fold and over-allocates contracts
  by the same factor. Calibrate the strike's own IV first with
  `options-implied-volatility-surface-construction`.
- **For single-name idiosyncratic risk.** The overlay hedges an index shock. A
  concentrated single-name blowup with a flat index leaves the puts worthless and
  the premium spent.
- **For American-style or physically settled contracts** without additional
  handling. The pricer assumes European exercise and cash settlement — no early
  exercise, no assignment. See `american-vs-european-style-option-exercise-handling`
  and `options-pin-risk-management-at-expiry`.
- **As an execution or mark-to-market engine.** No bid/ask, no commissions, no
  margin, no partial fills. Stress payoffs are terminal intrinsic values; a crash
  before expiry leaves the put worth more than intrinsic, so the figures are a
  floor, not a forecast.

## Prerequisites

- Portfolio value and the current level of the hedged index, in the same currency.
- **The implied volatility of the strike being bought**, from a calibrated surface.
  There is no default and no fallback.
- The underlying's dividend/carry yield (`dividend_yield`), if it pays one.
- The contract multiplier from the contract specification. 100 is the OCC standard
  for listed US equity options and the CBOE SPX multiplier, but corporate-action-adjusted
  contracts can deliver a non-standard amount.
- Python 3.9+. Standard library only.

## Workflow

1. **State the budget as annual, and state the roll schedule.** `budget_pct` is the
   premium ceiling per *year*. `dte_target` and `roll_dte` set the holding period
   (`dte_target - roll_dte`) and therefore how many tranches that budget must fund.
   At the defaults — buy 90 DTE, roll at 30 DTE — a tranche is held 60 days and the
   program buys 6.08 tranches a year, so each tranche gets 1/6.08 of the budget. If
   you shorten the holding period, each tranche gets *less*, not the same.
2. **Select the strike, then fetch that strike's implied volatility.** Do not reuse
   the ATM quote and do not reuse yesterday's. If the surface is stale or the strike
   is not quoted, stop — sizing on a guessed vol is worse than not hedging this cycle.
3. **Price the contract.** `black_scholes_put` returns price and Greeks per share;
   multiply by the contract multiplier for a per-contract premium. Pass
   `dividend_yield` for a dividend-paying index or the put is under-priced.
4. **Size against both constraints.** The contract count is the smaller of what the
   tranche budget affords and what the hedge-notional cap permits. Read
   `binding_constraint` before acting: `BUDGET` means protection is limited by
   spend, `NOTIONAL_CAP` means the budget would have bought a position larger than
   the portfolio it hedges — which is a leveraged short, not a hedge.
5. **Check `annualized_carry_pct`, not `carry_cost_pct`.** The first is the tranche
   cost projected across the roll cycle and is the number that must sit inside the
   policy budget. The second describes one tranche and will always look reassuringly
   small.
6. **Read stress payoffs net.** `stress_scenarios[...].net_coverage_ratio` is
   `(gross payout − premium) / portfolio loss`. At a shallow shock it is negative:
   the strike is never reached and the premium is a pure loss. That is the expected
   behaviour of a tail hedge, and seeing it is the point.
7. **Re-run every roll.** Spot, vol and portfolio value have all moved. A constant
   premium budget buys a *varying* amount of protection — least of it exactly when
   volatility has already spiked. If a minimum protection floor matters more than a
   fixed cost, size to the floor and let the budget be the output instead.

## Common Pitfalls

- **Spending the annual budget on every tranche.** The single most expensive error
  here. A 2% annual budget spent per 90-DTE tranche on a 60-day roll cycle realises
  roughly 12% of annual drag — six times the stated policy limit, and it compounds
  silently because each individual tranche looks compliant.
- **Pricing an OTM put at ATM volatility.** Since 1987 the index smile has been an
  asymmetric smirk with deep-OTM puts carrying the highest implied volatilities. At
  spot 400, 15% OTM, 90 DTE, the contract costs about \$61 at 20% vol and \$334 at
  30% vol. Using the wrong vol does not shade the answer, it multiplies it.
- **Sizing on budget alone.** Cheap deep-OTM puts let a budget-only sizer buy far
  more contracts than the portfolio has shares. At the module's own defaults this
  reached 394% of portfolio notional — a position that makes money in a crash
  because it is short the market, not because it is hedged.
- **Reading crash payouts gross.** A gross intrinsic payout looks like generous
  coverage until the premium is subtracted, and the premium is paid every cycle
  whether or not the crash arrives.
- **Assuming theta forces the roll.** Theta acceleration into expiry is an
  at-the-money phenomenon; a deep-OTM put has little extrinsic value to lose and its
  absolute theta stays small. The reason to roll a tail hedge at 30 DTE is that a
  short-dated, far-OTM put has almost no gamma and almost no vega left — it has
  stopped being convex, which is the only thing it was bought for.
- **Silent NaN.** A NaN volatility compares False against every bound, so a naive
  `if price <= 0` guard passes it through and the failure surfaces later, as a
  crash inside integer contract division or as a plan whose every field is NaN.
  This module raises `ValueError` at the boundary instead.
- **Assuming a 100-share deliverable.** Corporate-action-adjusted OCC contracts keep
  the 100 multiplier but can deliver a different amount; read the adjusted contract
  specification rather than hard-coding the assumption.

## Verification

Run the test suite from the skill's `scripts/` directory:

```bash
python -m unittest discover -s skills/tail-risk-hedging-with-options/scripts -v
```

38 tests. Pricing expectations are derived independently of the implementation —
by put-call parity against a separately written call formula, by central finite
differences of the price for Delta/Gamma/Vega/Theta, and by closed-form bounds — so
they cannot pass by restating the module's own algebra. Four suites are explicit
regressions and fail against the pre-2.0.0 behaviour: put delta sign, budget
annualisation, the notional cap, and non-finite input handling.

Sign-off gates are in `assets/checklist.md`; the roll-cycle procedure and its
evidence base are in `references/`.

## Related Skills

- `options-implied-volatility-surface-construction` — produces the per-strike IV this skill requires as input.
- `greeks-based-portfolio-hedging-automation` — consumes the overlay's Greeks at portfolio level.
- `real-time-greeks-recalculation-on-market-moves` — keeps those Greeks current between rolls.
- `tail-correlation-between-strategies-under-stress` — why diversification fails in the regime this overlay is bought for.
- `portfolio-stress-test-including-liquidity-crunch-scenarios` — stress framework the payoff table feeds into.
- `scenario-based-stress-testing-custom-shocks` — for shocks beyond the fixed -10/-20/-30/-40% grid.
- `american-vs-european-style-option-exercise-handling` — required before applying this pricer to American-style contracts.
- `options-pin-risk-management-at-expiry` — expiry handling this skill deliberately excludes.
