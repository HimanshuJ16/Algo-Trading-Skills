---
name: portfolio-stress-test-including-liquidity-crunch-scenarios
description: Use when stress-testing a portfolio against a scenario that combines a price
  shock with a liquidity crunch, reporting the stressed mark-to-market loss, the cost of
  liquidating into the crunch, and the Days-to-Liquidate (DTL) horizon per position.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- stress-testing
- liquidity-crunch
- days-to-liquidate
- liquidation-cost
- market-impact
- square-root-law
brokers_frameworks:
- Portfolio Liquidity Stress Engine
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a scenario P&L is not enough — when the question is not only
"what is the book worth after the shock?" but "what does it cost to get out, and how
long am I stuck?" A conventional VaR or scenario revaluation marks positions at shocked
prices and stops there, implicitly assuming the book can be sold at those prices on
demand. In a crunch that assumption is where the loss actually lives.

The engine reports three figures that are never merged:

1. **Price shock loss** — the stressed mark-to-market, netting longs against shorts.
2. **Liquidity haircut** — the spread and market-impact cost of liquidating into the
   crunch.
3. **Days-to-Liquidate** — the horizon each position implies at a bounded participation
   rate against a haircut ADV, plus a bottleneck flag.

Keep them separate: a book can be perfectly resilient to the shock and still be
untradeable, and those two findings call for different responses.

## When NOT to Use

- **As a capital requirement or a regulatory stress test.** No regulator-set methodology
  is implemented here. ESMA's liquidity-stress-testing guidelines bind UCITS and AIF
  managers, not proprietary traders — see `references/standards.md` for what actually
  applies to whom.
- **As a transaction cost model for live execution.** The liquidity haircut is a
  conservative, scenario-conditioned estimate for a fire-sale exit, not a pre-trade cost
  forecast. Use `transaction-cost-analysis-tca-integration`.
- **As a position sizer.** It measures an existing book. Capping a position at what the
  instrument can absorb is `liquidity-adjusted-position-sizing`.
- **To model correlated crowding or a margin spiral.** Shocks are applied independently
  per symbol from the scenario vector; there is no forced-seller feedback, no funding
  channel, and no assumption that everyone in the same trade exits through the same door.
  See `tail-correlation-between-strategies-under-stress`.
- **On a book that has not been netted per instrument.** Two rows for the same symbol
  each compute DTL against the full ADV, understating the true horizon. The engine
  raises rather than letting that through.
- **To generate the scenario.** The shock vector, the capacity haircut and the spread
  expansion are your judgement. ESMA34-39-897 para. 45 is explicit that managers "should
  not only refer to historical observations of stressed markets".

## Prerequisites

- Positions netted per instrument: `symbol`, `quantity` (signed), `current_price`,
  `adv_shares`, `spread_bps`, and optionally `daily_volatility`.
- `current_price` and `adv_shares` in the **same unit** — price per contract with ADV in
  contracts for derivatives, or the position is stressed at a fraction of its real size.
- A `StressScenario`: `price_shock_pct` per symbol (a `DEFAULT` key covers the rest),
  `liquidity_drop_pct` (default $0.50$), `spread_expansion_factor` (default $5.0$).
- A `Config`: `max_allowed_dtl_days` (default $5.0$), `daily_participation_rate`
  (default $0.10$), `impact_coefficient_y` (default $1.0$). All three are **library
  defaults, not regulatory limits** — calibrate and record why.

## Workflow

1. **Compute the stressed liquidity state and DTL**:
   $$\text{StressedADV}_i = \text{ADV}_i (1 - \text{LiquidityDrop}), \qquad
     \text{Capacity}_i = \alpha \cdot \text{StressedADV}_i, \qquad
     DTL_i = \frac{|Q_i|}{\text{Capacity}_i}$$
   - **Decision point — `liquidity_drop_pct` is a *capacity* haircut, not a volume
     forecast.** In March 2020 volume *rose* — venues handled "record trading volumes"
     — while depth collapsed to 2008 levels, with 10-year UST depth down $93\%$ from its
     February average (FSB, *Holistic Review of the March Market Turmoil*, 2020). Size
     the haircut from the loss of absorbable size at a tolerable price. Sizing it from an
     observed decline in tape volume understates the crunch, because in the episodes this
     skill models the tape got *busier* while the book got thinner.
   - A zero or negative ADV raises. An instrument with no volume has no finite DTL;
     handle it as unliquidatable rather than accepting a fabricated number.

2. **Revalue at shocked prices, with the sign preserved**:
   $$\text{PriceShockLoss} = -\sum_i Q_i P_i \Delta_i$$
   - Positive is a loss. Because $Q_i$ is signed, a long gains on a rally and a short
     gains on a crash, so a hedged book nets.
   - **Decision point — netting is only as real as the shock vector.** Offsetting legs
     net to zero here only if you shocked them consistently. A hedge that nets on paper
     can still gap in a crunch; if you do not believe the offset, shock the legs apart.
   - **Decision point — a symbol absent from the scenario raises.** Do not paper over it
     with a default shock nobody chose; either add the symbol or set `DEFAULT` explicitly.

3. **Price the liquidation, in two separately-reported components**:
   $$\text{SpreadCost}_i = \tfrac{1}{2}\cdot\frac{\text{Spread}_i \cdot \text{Expansion}}{10^4}\cdot |Q_i| P_i
     \qquad\text{(Bangia et al., 1999)}$$
   $$\text{Impact}_i = Y \sigma_i \sqrt{\phi_i}\cdot |Q_i| P_i,
     \qquad \phi_i = \frac{|Q_i|}{\text{StressedADV}_i}
     \qquad\text{(Tóth et al., 2011, Eq. 1)}$$
   - **Decision point — the spread is charged once per share, not once per share per
     day.** A liquidation crosses from the mid to the bid once, which is why the
     canonical exogenous cost is *half* the spread on the position value. Slicing over
     $DTL$ sessions does not make each share pay the spread $DTL$ times. Version 1.0.0
     charged the full spread on the full position for each of up to ten days,
     overstating this component by up to $20\times$; the horizon belongs in the impact
     term, not here.
   - **Decision point — impact is priced only where `daily_volatility` is supplied.**
     Positions without it appear in `positions_missing_volatility` and contribute zero,
     so the haircut is an explicit *lower bound* rather than a silent zero.
   - **Decision point — check `positions_outside_impact_calibration` before quoting the
     impact number.** Tóth et al. fit $\phi$ over "a few $10^{-4}$ to a few %". A
     stressed book routinely implies $\phi > 1$. Beyond $\phi = 0.10$ the figure is an
     extrapolation: read it as an order-of-magnitude flag that the position is
     untradeable in the assumed horizon, not as a cost estimate.

4. **Audit the bottlenecks**: flag $DTL_i > \text{MaxAllowedDTL}$ (strictly greater —
   exactly at the limit passes) and emit `LIQUIDITY_CRUNCH_ILLIQUID_WARNING`.
   - **Decision point — the warning is about the horizon, not the loss.** It fires on
     tradeability alone. A book with a small stressed loss and a 40-day exit has passed
     the P&L test and failed the one that matters in a crunch.

5. **Report per position and in aggregate**: `positions` carries the per-symbol
   breakdown so any aggregate can be traced to its drivers.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Booking a loss on a favourable shock.** Taking $|{\Delta}|$ instead of the signed
  return makes every long lose on a rally and every short lose on a crash, and no book
  can ever net. Version 1.0.0 did exactly this: a market-neutral pair under a $-20\%$
  shock reported a $20\%$ loss on gross rather than approximately zero. Multiply the
  signed quantity by the signed return and flip the sign once at the end.
- **Charging the spread per day of the liquidation.** Dimensionally this is dollars ×
  days: there is no per-day rate to justify it, and the error grows with exactly the
  positions the report is meant to flag. Charge half the spread once per share; put the
  horizon in the impact term.
- **Modelling the crunch as a volume decline.** Crash-period volume typically rises. The
  binding constraint is depth, and haircutting ADV is a *proxy* for it — a proxy that is
  understated if you calibrate it against observed tape volume.
- **Reading a NaN as a pass.** Every comparison against NaN is False, so an unguarded NaN
  price or ADV clears `price <= 0`, clears `dtl > max_allowed`, and lands in a report
  whose status reads `STRESS_TEST_PASSED`. A stress test that answers "passed" on corrupt
  reference data is worse than none, because the caller has been told the book survives.
- **Flooring a zero ADV to one share a day.** It converts an untradeable instrument into
  a large-but-finite DTL derived from a volume that does not exist.
- **Splitting one holding across rows.** Two 50,000-share rows against a 50,000-share ADV
  report 20 days each; the real 100,000-share position takes 40. Net per instrument first.
- **Quoting the impact number outside its fitted range.** At $\phi = 4$ the square-root
  law is being extrapolated roughly two orders of magnitude beyond the data it was fitted
  on. The honest reading is "this cannot be liquidated on this horizon", not a dollar
  figure.
- **Treating the defaults as standards.** $10\%$ participation, $5$ days, $50\%$ capacity
  drop and $5\times$ spreads are library defaults. None is regulator-set. An uncalibrated
  default that has never been questioned is a scenario nobody chose.
- **Mismatching price and ADV units on derivatives.** Price per share with ADV in
  contracts understates the position by the contract multiplier — on precisely the
  instruments most likely to be the bottleneck.

## Verification

Run `python -m unittest discover -s skills/portfolio-stress-test-including-liquidity-crunch-scenarios/scripts`
and confirm a 100% pass rate. The suite pins the behaviour below.

- **Worked illiquid example.** $100{,}000$ shares @ $\$10$ (gross $\$1$M), $\text{ADV} =
  50{,}000$, $20$ bps spread; scenario $-30\%$, $50\%$ capacity drop, $5\times$ spreads.
  Stressed ADV $25{,}000$, capacity $2{,}500$/day $\Rightarrow DTL = 40.0$ days,
  `LIQUIDITY_CRUNCH_ILLIQUID_WARNING`. Price shock loss $\$300{,}000$; spread cost
  $0.5 \times 100\text{bps} \times \$1\text{M} = \$5{,}000$; total $\$305{,}000$.
- **Sign convention.** A $1{,}000$-share long @ $\$100$ returns $+\$10{,}000$ on a
  $-10\%$ shock and $-\$10{,}000$ (a gain) on a $+10\%$ shock; the mirror short returns
  the opposite. A long/short pair under a common $-20\%$ shock nets to exactly $0.0$.
- **Spread cost is horizon-independent.** $1{,}000$ shares @ $\$100$, $10$ bps, no
  expansion: $\$50.00$ whether $DTL$ is $0.001$ or $20$ days — and not the $\$1{,}000$
  that version 1.0.0's per-day charge produced.
- **Impact follows the square-root law.** $500$ shares @ $\$1{,}000$, stressed ADV
  $12{,}500 \Rightarrow \phi = 0.04$, $\sigma = 0.04$, $Y = 1$: impact $= 1.0 \times 0.04
  \times 0.2 \times \$500{,}000 = \$4{,}000$. Quartering the ADV quadruples $\phi$ and
  exactly doubles the impact.
- **Threshold is strict.** $DTL$ of exactly $5.0$ against a $5.0$-day limit passes;
  $5.005$ warns.
- **Negative checks.** NaN/$\pm\infty$/numeric-string/bool inputs, a non-positive price
  or ADV, a negative spread or volatility, a blank symbol, `liquidity_drop_pct` outside
  $[0, 1)$, a shock below $-1.0$, a participation rate outside $(0, 1]$, a non-positive
  `max_allowed_dtl_days`, a duplicated symbol, and a symbol absent from the scenario with
  no `DEFAULT` key must each raise `ValueError`.

## Related Skills

- `liquidity-adjusted-position-sizing`
- `scenario-based-stress-testing-custom-shocks`
- `stress-testing-against-historical-crash-scenarios`
- `real-time-liquidity-risk-monitoring`
- `tail-correlation-between-strategies-under-stress`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `value-at-risk-var-live-monitoring`
