---
name: greeks-based-portfolio-hedging-automation
description: Use when managing options or multi-asset portfolios to aggregate net
  portfolio Greeks (Delta, Vega) with contract multipliers and beta weighting, evaluate
  risk tolerance breaches, and automatically generate rebalancing hedge orders to
  maintain delta/vega neutrality.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- options-hedging
- portfolio-greeks
- delta-neutral
- vega-hedging
- automated-rebalancing
brokers_frameworks:
- Greeks Hedging Engine
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating options portfolios, market-making books, or delta-neutral strategies. Price movements in underlying assets alter portfolio delta ($\Delta$) and vega ($\nu$) in real time. Unhedged delta exposure leaves the portfolio vulnerable to directional market shocks. This skill aggregates net portfolio Greeks using each contract's own multiplier, expresses cross-underlying exposure in beta-weighted index-equivalent terms, monitors risk tolerance bands, and calculates the hedge orders (underlying, futures, or an options overlay) needed to restore neutrality.

## When NOT to Use

- **As a substitute for gamma, theta, or rho management.** Only delta and vega are aggregated. A book neutralised here can still be short gamma, in which case the hedge is stale the moment spot moves and re-hedging is itself a loss-making activity. Pair with `real-time-greeks-recalculation-on-market-moves`.
- **When the Greeks you have are stale or from an inconsistent surface.** The engine performs no pricing and cannot detect a bad vega or a mismarked delta; it converts whatever it is given into an order. Build the surface first — `options-implied-volatility-surface-construction`.
- **As a kill switch or drawdown control.** Hedging reduces *directional* exposure; it does not cap loss, and a delta-neutral book still bleeds through the spread on every rebalance. Circuit breakers must be independent — `kill-switch-and-drawdown-circuit-breakers`.
- **When you cannot supply a per-position `beta` against the hedge instrument.** The default of $1.0$ means "this name moves one-for-one with the hedge proxy," which is false for most single names and will mis-size the hedge in the direction of your largest high-beta position.
- **Near expiry on at-the-money strikes.** Delta becomes discontinuous through the pin and a first-order hedge chases it — see `options-pin-risk-management-at-expiry`.

## Prerequisites

- Per-position Greeks **per unit of the deliverable** ($\Delta_i, \nu_i$), quantities (signed: long $+Q$, short $-Q$), and spot prices.
- **Contract multiplier $M_i$ per position** — read from the contract master, never assumed. 100 for a standard US equity option, but OCC-adjusted contracts deliver other amounts, and index futures carry their own multiplier.
- **Beta $\beta_i$ of each underlying against the delta hedge instrument's underlying**, when the book spans more than one name.
- Limits: $\Delta_{\text{max\_usd}}$ (the hedge trigger), $\nu_{\text{max\_usd}}$, and $\Delta_{\text{min\_rebalance}}$ (the minimum order size, not a trigger).
- Hedge instrument terms: price, multiplier, delta per unit, and vega per unit for any options overlay.

## Workflow

1. **Aggregate Net Portfolio Greeks** (multiplier applied to *both* Greeks):
   $$\Delta_{\text{net\_usd}} = \sum_{i=1}^N Q_i M_i \Delta_i S_i, \quad \nu_{\text{net\_usd}} = \sum_{i=1}^N Q_i M_i \nu_i$$
   - **Decision point — reject, do not net.** A NaN delta, a zero price, or a delta quoted as `60` instead of `0.60` must raise before aggregation. A corrupt position silently netted into the total produces a confidently wrong order, which is worse than no hedge at all.

2. **Beta-Weight to the Hedge Instrument**:
   $$\Delta_{\beta\text{-w}} = \sum_{i=1}^N \beta_i \, Q_i M_i \Delta_i S_i$$
   - **Decision point — hedge the beta-weighted number, report both.** The raw sum is the notional view; the beta-weighted sum is the exposure the index proxy can actually offset. Publish the per-underlying breakdown too: one concentrated high-beta name inside an apparently flat book is a hedge that will not behave as the total suggests.

3. **Evaluate Hedge Trigger Bands**:
   Hedge only when $|\Delta_{\beta\text{-w}}| > \Delta_{\text{max\_usd}}$, or $|\nu_{\text{net\_usd}}| > \nu_{\text{max\_usd}}$.
   - **Decision point — the trigger is the limit, not the minimum order size.** $\Delta_{\text{min\_rebalance}}$ suppresses *small orders*; using it as the trigger hedges every drift and converts the spread into a standing charge on the book.

4. **Size the Vega Leg First** (it injects delta):
   $$n_\nu = \text{trunc}\left(\frac{-\nu_{\text{net\_usd}}}{\nu_{\text{hedge}} M_{\text{hedge}}}\right), \quad \Delta_{\text{injected}} = n_\nu \, \Delta_{\text{hedge}} M_{\text{hedge}} S_{\text{hedge}}$$
   - **Decision point — vega cannot be hedged with a linear instrument.** If no vega-carrying overlay is supplied, emit an explicit unhedged-vega warning and escalate. Do not silently return an empty order list on a vega breach, and never substitute a delta trade for it.

5. **Size the Delta Leg on the Post-Vega Exposure**:
   $$n_\Delta = \text{trunc}\left(\frac{-(\Delta_{\beta\text{-w}} + \Delta_{\text{injected}})}{\Delta_{\text{hedge}} M_{\text{hedge}} S_{\text{hedge}}}\right)$$
   - **Decision point — truncate toward zero, never round.** Rounding up overshoots past neutral and flips the book to the opposite sign, an exposure the risk limit never authorised. Truncation leaves a residual in the original direction, which is bounded and reportable.

6. **Emit Orders, Residual, and Audit Trail**:
   Report $\Delta_{\text{residual}}$, $\nu_{\text{residual}}$, and whether they are inside the limits.
   - **Decision point — an empty order list is not the same as "flat".** If one hedge contract carries more delta than the entire breach, no whole-contract hedge exists; the breach must be reported as unhedgeable, not returned as silence. Route the resulting orders through the same pre-trade controls, client-order-ID idempotency, and kill-switch coverage as any strategy order.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting the contract multiplier**: computing dollar delta as $Q \times \Delta \times S$ treats a contract count as a share count. For a standard US equity option that understates exposure by $100\times$ and produces a hedge $1/100$ of the size required — the book is effectively unhedged while the report claims otherwise.
- **Hard-coding the multiplier at 100**: Cboe equity options deliver "generally 100 shares", but OCC adjusts the deliverable after splits, mergers, and special distributions and publishes the new terms in an Information Memo. An adjusted contract delivering 10 shares hedged as if it delivered 100 is a $10\times$ error. Read the multiplier from the contract master per position.
- **Ignoring futures and index multipliers**: E-mini S&P 500 is $\$50$ per index point. NSE revises index-derivative lot sizes periodically to keep contract value above SEBI's minimum, so a NIFTY multiplier hard-coded from an old circular goes stale without any error — take it from the current contract specification.
- **Applying the multiplier to delta but not vega (or vice versa)**: both Greeks are per deliverable unit, so both need $M$. Vega is already quoted per one percentage point of implied volatility; there is no additional factor of 100 to apply on top of it.
- **Summing dollar delta across underlyings at an implied beta of 1.0**: aggregating a high-beta name and a defensive name into one total and hedging it with an index proxy under-hedges the beta the book actually carries. Beta-weight before sizing.
- **Over-hedging on high-frequency friction**: rebalancing on tiny delta drifts accumulates bid-ask spread cross costs that can exceed the risk reduction achieved. Trigger on the limit; suppress orders below the minimum rebalance size.
- **Rounding the hedge quantity to nearest**: rounding $-2.8$ contracts to $-3$ overshoots past neutral and leaves the book short an exposure nobody sized. Truncate toward zero and report the residual.
- **Treating an empty order list as a clean bill of health**: a $\$60{,}000$ breach against a $\$250{,}000$-per-contract future generates no order at all. Without an explicit residual and warning, an agent reads that as "no action needed" and the breach persists indefinitely.
- **Sizing the delta leg before the vega leg**: an options vega overlay carries delta. Hedge delta first and the overlay re-opens a delta hole the size of the overlay's own exposure.

## Verification

- **Multiplier regression**: 1,000 SPY calls, $M=100$, $\Delta=0.60$, $S=\$500$ $\implies$ `net_delta_usd` $= \$30{,}000{,}000$ and a `SELL 60,000` SPY share hedge at $\$500$. Computing without $M$ yields $\$300{,}000$ and 600 shares — a hedge two orders of magnitude too small.
- **Vega scaling**: an OCC-adjusted contract delivering 10 shares, 500 contracts at $\nu=0.30$ $\implies$ `net_vega_usd` $= \$1{,}500$, not $\$15{,}000$.
- **Trigger semantics**: $\$20{,}000$ of delta against a $\$50{,}000$ limit and a $\$10{,}000$ minimum order size $\implies$ `is_hedging_required` is `False` and no order is generated.
- **Beta weighting**: 100 contracts, $M=100$, $\Delta=0.50$, $S=\$150$, $\beta=1.72$ $\implies$ raw $\$750{,}000$, beta-weighted $\$1{,}290{,}000$, hedge `SELL 2,580` SPY at $\$500$.
- **Truncation**: $\$700{,}000$ of delta hedged with E-mini S&P 500 at 5,000 ($\$250{,}000$/contract) $\implies$ `SELL 2` contracts with `residual_delta_usd` $= +\$200{,}000$ and `is_residual_within_limits` `False` — never `SELL 3`.
- **Vega leg sequencing**: a $-\$20{,}000$ vega book hedged with a $\$60$-vega, $\$25{,}000$-delta overlay $\implies$ `BUY 333` overlay contracts (residual vega $-\$20$) followed by `SELL 16,650` SPY shares to remove the $\$8{,}325{,}000$ of injected delta.
- **Negative checks**: a NaN/Inf Greek, a delta of `60`, a non-positive spot or multiplier, a missing position or hedge-instrument multiplier, a zero-priced hedge instrument, and a non-positive risk limit must each raise.
- Run `python scripts/test_greeks_hedging_engine.py` and confirm 100% pass rate.

## Related Skills

- `options-greeks-real-time-portfolio-aggregation`
- `real-time-greeks-recalculation-on-market-moves`
- `options-implied-volatility-surface-construction`
- `options-pin-risk-management-at-expiry`
- `kill-switch-and-drawdown-circuit-breakers`
- `options-backtesting-with-realistic-iv-surface`
