# Deep Workflow Reference — greeks-based-portfolio-hedging-automation

This file holds the full technical procedure referenced by `SKILL.md`.

## Scaling conventions

All per-position Greeks are quoted **per unit of the deliverable** (per share for a
standard equity option), never per contract. The contract multiplier $M_i$ converts
contract count to deliverable units and applies to *every* Greek:

$$\Delta_{i,\text{usd}} = Q_i M_i \Delta_i S_i, \qquad \nu_{i,\text{usd}} = Q_i M_i \nu_i$$

Sign is carried entirely by $Q_i$ — short positions are negative quantities, not
negated Greeks. Vega is quoted per one percentage point (one vol point) of implied
volatility, so $\nu_{\text{net\_usd}}$ is the P&L of a one-vol-point parallel shift.
No additional factor of 100 is applied anywhere.

$M_i$ is a per-position input read from the contract master. It is 100 for a
standard US equity/ETP option, but OCC contract adjustments after splits, mergers
and special distributions produce non-standard deliverables, and index futures
carry their own multiplier ($\$50$ per index point for CME E-mini S&P 500).

## Full Procedure

1. **Validate before aggregating.** Reject any position with a non-finite Greek, a
   non-positive spot or multiplier, or $|\Delta_i| > 1$ (a delta of `60` means the
   feed quoted percent). A corrupt position must raise, never net into the total.

2. **Aggregate raw and beta-weighted dollar delta, and dollar vega.**
   $$\Delta_{\text{net\_usd}} = \sum Q_i M_i \Delta_i S_i, \quad
     \Delta_{\beta\text{-w}} = \sum \beta_i Q_i M_i \Delta_i S_i, \quad
     \nu_{\text{net\_usd}} = \sum Q_i M_i \nu_i$$
   $\beta_i$ is measured against the *delta hedge instrument's* underlying. Report
   the per-underlying delta breakdown alongside the totals. Sum with `math.fsum`:
   the trigger is a threshold comparison, and a large book netting near zero must
   not flip across the limit on position ordering.

3. **Evaluate trigger bands.** Hedge when $|\Delta_{\beta\text{-w}}| > \Delta_{\text{max\_usd}}$
   or $|\nu_{\text{net\_usd}}| > \nu_{\text{max\_usd}}$. $\Delta_{\text{min\_rebalance}}$
   is a floor on order size, not a trigger.

4. **Size the vega leg first.** An options overlay carries delta, so it must be
   sized before the delta leg:
   $$n_\nu = \text{trunc}\left(\frac{-\nu_{\text{net\_usd}}}{\nu_{\text{hedge}} M_{\text{hedge}}}\right),
     \qquad \Delta_{\text{injected}} = n_\nu \Delta_{\text{hedge}} M_{\text{hedge}} S_{\text{hedge}}$$
   With no vega-carrying instrument supplied, emit `VEGA_BREACH_UNHEDGED` and
   escalate — a linear instrument cannot neutralise vega.

5. **Size the delta leg on the post-overlay exposure.**
   $$n_\Delta = \text{trunc}\left(\frac{-(\Delta_{\beta\text{-w}} + \Delta_{\text{injected}})}{\Delta_{\text{hedge}} M_{\text{hedge}} S_{\text{hedge}}}\right)$$
   Truncation toward zero guarantees the hedge never overshoots past neutral.
   Suppress the order when its own dollar delta is below $\Delta_{\text{min\_rebalance}}$.

6. **Report residual and emit the audit trail.** Publish $\Delta_{\text{residual}}$,
   $\nu_{\text{residual}}$, `is_residual_within_limits`, and every warning. Hedge
   orders are recommendations: routing them requires the same pre-trade risk
   controls, client-order-ID idempotency and kill-switch coverage as strategy
   orders.

## Warning codes

| Code | Meaning | Operator action |
|---|---|---|
| `VEGA_BREACH_UNHEDGED` | Vega limit breached, no vega-carrying instrument supplied | Supply an options overlay or escalate to a human risk manager |
| `VEGA_HEDGE_ROUNDS_TO_ZERO` | One overlay contract carries more vega than the breach | Use a smaller-vega overlay or accept the breach explicitly |
| `DELTA_BREACH_UNHEDGEABLE` | One hedge contract carries more delta than the breach | Switch to Micro futures or the cash underlying |
| `DELTA_HEDGE_INSTRUMENT_HAS_NO_DELTA` | Hedge instrument carries zero dollar delta per unit | Fix the instrument definition |
| `HEDGE_SUPPRESSED_BELOW_MIN_SIZE` | Hedge is smaller than the minimum rebalance size | Expected fee-drag suppression; confirm the residual is tolerable |

An empty `recommended_hedge_orders` list with `is_hedging_required` true always
carries one of these codes. It never means "flat".

## Production Implementation Reference

- Reference code: `scripts/greeks_hedging_engine.py`
  (`GreeksPortfolioHedgingEngine`, `OptionPosition`, `HedgeInstrument`,
  `NetGreeksSummary`, `HedgeOrder`, `HedgingAuditReport`).
- Automated unit tests: `scripts/test_greeks_hedging_engine.py`.

## Sources

- Cboe, *Equity Options Specifications* — standard deliverable is "generally 100
  shares of one of the exchange-traded products".
  https://www.cboe.com/exchange-traded-stock/equity-options-spec/
- OCC, Information Memo #26853, *Contract Adjustments* — adjusted contracts carry
  non-standard deliverables; OCC publishes the revised terms per event.
  https://infomemo.theocc.com/infomemos?number=26853
- CME Group, *E-mini S&P 500 Futures Contract Specifications* — $\$50$ per index
  point, 0.25-point tick ($\$12.50$).
  https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.contractSpecs.html
- OIC (Options Industry Council), *Vega* — vega is the change in option value for a
  1% (one vol point) change in implied volatility.
  https://www.optionseducation.org/advancedconcepts/vega
- Cboe Insights, *How to Right-size Hedges Via Beta Weighting with XSP Options* —
  beta-weighted delta converts a position's sensitivity into index-equivalent terms
  before hedge sizing.
  https://www.cboe.com/insights/posts/how-to-right-size-hedges-via-beta-weighting-with-xsp-options/
