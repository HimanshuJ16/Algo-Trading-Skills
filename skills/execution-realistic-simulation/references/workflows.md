# Deep Workflow Reference — execution-realistic-simulation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Directional Spread Filling:**
   - Fill BUY orders at or beyond the ask and SELL orders at or beyond the bid. Never
     fill at mid-price.
   - Validate the side string against an explicit allow-list. An execution simulator
     that treats any unrecognised string as one direction turns a typo into a backtest
     that trades the wrong way, with no error visible in the results.

2. **Square-Root Market Impact Modeling:**
   - Model impact with the square-root law via `RealisticExecutionSimulator`:

     $$\text{fill}_{\text{BUY}} = P_{\text{mid}} + \tfrac{\text{spread}}{2} + \gamma \sigma \sqrt{\tfrac{Q}{V}} P_{\text{mid}}$$

   - $Q$ is the executed metaorder size and $V$ the average daily volume in the same
     units; $\sigma$ is **daily** volatility as a fraction, on the same horizon as $V$.
   - $\gamma$ is dimensionless and empirically of order 1; published calibrations
     cluster in 0.5–1.0, which is the default's basis. It is a free parameter, not a
     measurement — fit it to your own realized fills.
   - The law is estimated on metaorders small relative to daily volume. Above ~10%
     participation the helper logs a warning; treat the number as an extrapolation.

3. **Liquidity Depth & Partial Fill Simulation:**
   - If $Q > \text{Depth}_{\text{available}}$, set `is_partial_fill=True` and bound the
     filled quantity to the available depth.
   - Charge impact and fees on the quantity that actually traded. An order that filled
     nothing incurs nothing — a flat per-order brokerage on a zero-quantity fill is a
     fee for a trade that never happened.
   - This truncates at a single aggregate depth figure; it does not walk a multi-level
     book. See `order-book-depth-processing-l2-l3` for that.

4. **Complete Regulatory & Statutory Fee Stack:**
   - Compute the full breakdown via `FeeBreakdown`. GST applies to brokerage +
     exchange transaction charges + SEBI turnover fee only; STT and stamp duty are
     themselves taxes and are outside the GST base. Stamp duty is a buy-side charge.
   - Rates in `DEFAULT_FEE_SCHEDULES`, verified 2026-08-24:

     | Segment | STT / CTT | NSE txn charge | SEBI | Stamp (buy) | GST |
     |---|---|---|---|---|---|
     | Equity options | 0.15% sell (premium) | 0.03553% of premium | ₹10/cr | 0.003% | 18% |
     | Equity futures | 0.05% sell | 0.00183% | ₹10/cr | 0.002% | 18% |
     | Equity intraday | 0.025% sell | 0.00307% | ₹10/cr | 0.003% | 18% |
     | Equity delivery | 0.1% both sides | 0.00307% | ₹10/cr | 0.015% | 18% |

   - The F&O STT rates above took effect **1 April 2026** (Budget 2026). They replaced
     the 1 October 2024 rates (0.10% options / 0.02% futures), which had themselves
     replaced 0.0625% / 0.0125%. Equity delivery and intraday STT were unchanged.
   - NSE transaction charges include IPFT. Brokerage defaults follow a published
     discount-broker schedule (flat ₹20 per executed order on options; 0.03% capped at
     ₹20 on intraday and futures; zero on delivery) and are commercial, not statutory —
     override them with your own broker's terms.
   - `US_EQUITY` models the SEC Section 31 fee (USD 20.60 per USD 1,000,000 of sales,
     sell side only, effective 4 April 2026) and defaults commission to zero.
   - `CRYPTO_SPOT` ships a 0.1% placeholder only. Venue fees are tier- and
     maker/taker-dependent; replace it before trusting any cost figure
     (`exchange-fee-tier-and-rebate-structure-analysis`).

5. **Rate Provenance:**
   - Every schedule carries `effective_from` and `source`, and the module carries
     `FEE_SCHEDULES_VERIFIED_ON`. Re-verify against the exchange's and the tax
     authority's current published rates before using cost figures in a decision, and
     record the verification date. A bare rate constant with no date is the defect —
     the wrong number is only its symptom.

6. **Execution Latency:**
   - The simulator does **not** model latency. It prices a fill against one quote
     snapshot. Latency is modelled by which snapshot you pass: use the mid and
     half-spread from the moment the order could actually have reached the exchange,
     after the bot's processing time, network latency to the broker, and broker-to-
     exchange latency.
   - Passing the signal bar's own quote reintroduces look-ahead regardless of how
     carefully impact and fees are modelled (`lookahead-bias-elimination`).

7. **Post-Trade Recalibration:**
   - Compare modelled fill prices against actual paper/live broker fills and recalibrate
     $\gamma$ where divergence is systematic rather than noise. `SimulatedFillResult`
     exposes `participation_ratio` and `market_impact_per_unit` so the modelled impact
     can be regressed against realized impact directly.
   - Cadence and statistical tests: `execution-cost-model-recalibration-cadence`.

## Known Failure Modes

- **Mid-Price Fill Assumption:** Filling all simulated orders at mid-price, overstating
  backtest performance for illiquid options.
- **Flat or Linear Slippage:** A constant ₹1 slippage, or impact linear in $Q/V$,
  regardless of size relative to ADV. Linear impact understates small orders and
  overstates large ones relative to the empirical square-root law.
- **Stale Statutory Rates:** Carrying a pre-October-2024 options STT rate of 0.0625%
  against the current 0.15% understates the dominant cost of an options-selling
  strategy by a factor of 2.4.
- **Fee Branch Fall-Through:** An instrument type with no explicit fee schedule
  inheriting another market's rates — a futures backtest priced at a crypto venue's
  0.1% taker fee with zero STT.
- **Placeholder ADV:** Flooring an unknown or zero ADV at 1.0, which turns any order
  into a 100%-participation impact estimate and applies a several-percent phantom price
  move to every affected fill.
- **Clamped Fill Prices:** Truncating an implausible modelled fill at a hard-coded floor
  (e.g. ₹0.01) instead of raising. The backtest then reports a price the model never
  produced, and the sizing error that caused it goes unnoticed.
- **Instantaneous Execution:** Assuming zero latency between signal bar close and order
  fill.

## Production Implementation Reference

- Reference code: `scripts/fill_model.py` (`RealisticExecutionSimulator`,
  `SimulatedFillResult`, `FeeBreakdown`, `FeeSchedule`, `DEFAULT_FEE_SCHEDULES`,
  `MarketType`).
- Automated unit tests: `scripts/test_fill_model.py`.
- `simulate_fill_price` and `estimate_fees` remain as deprecated shims for existing call
  sites and emit `DeprecationWarning`; new code should use the simulator, which returns
  fees, partial-fill state, and impact diagnostics alongside the price.
