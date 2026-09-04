---
name: weather-derivatives-and-niche-instrument-handling
description: >-
  Use when accumulating a heating or cooling degree-day or CAT index from station
  temperatures and pricing a CME weather future, option or capped OTC swap by burn
  analysis. Official settlement comes from the exchange's index provider.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: weather-derivatives, cme-weather, hdd-futures, cdd-futures, cat-index, burn-analysis, otc-swaps, niche-instruments
  brokers_frameworks: "CME Rulebook Ch. 403 (US Degree Days Index Futures); CME Rulebook Ch. 411 (Pacific Rim CAT Index Futures); Speedwell Settlement Services; ISDA (OTC weather swap documentation)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you need an accumulated weather index, a settlement payoff, or a
burn-analysis fair value for a CME weather futures/option position or a capped OTC
weather swap — pricing a new structure, marking a position, or reconciling a
settlement.

The engine provides:

- **Index accumulation** — HDD, CDD and CAT totals from daily $(T_{\min}, T_{\max})$
  station observations, under an explicitly supplied temperature unit and base.
- **Settlement payoffs** at the contract's own multiplier and currency, for futures,
  calls, puts and capped swaps.
- **Burn analysis** over historical seasons, reporting the expected payoff, the
  dispersion, and both tails of the realised payoff distribution.
- **Linear climate detrending** of a historical index record to the contract season.

**Multipliers and currency.** There is no universal "\$20 multiplier". The verified
values live in `CME_CONTRACT_SPECS`:

| Specification | Multiplier | Base temperature |
| :--- | :--- | :--- |
| `CME_US_DEGREE_DAY` (HDD, CDD) | **USD 20** / index point | $65^\circ\text{F}$ |
| `CME_EUROPEAN_HDD` | **EUR 20** / index point | $18^\circ\text{C}$ |
| `CME_EUROPEAN_CAT` | **EUR 20** / index point | none (CAT sums the daily mean) |
| `CME_PACIFIC_RIM_CAT` (Tokyo) | **JPY 2,500** / index point | none |

Build contracts with `WeatherDerivativeContract.from_spec(...)` so the multiplier and
currency come from the specification rather than from memory.

## When NOT to Use

- **To settle cash.** The official CME index is calculated and reported by Speedwell
  Settlement Services Ltd from National Weather Service / Japan Meteorological Agency
  observations, on the second Exchange Business Day after the contract month.
  Speedwell's published methodology governs rounding and the treatment of missing
  station observations, and this module reproduces neither. `calculate_monthly_index`
  is an estimate for pricing, hedging and pre-settlement reconciliation — settle
  against the reported index, and investigate a divergence rather than overriding it.
- **To price with Black-Scholes.** Weather is not a traded, storable asset, so there
  is no delta-hedging replication argument and no risk-neutral drift to solve for.
  Use burn analysis or a stochastic temperature model. See the pitfalls below.
- **As a tail-risk model.** Burn analysis has no distributional model. With 20–30
  seasons the 5th percentile rests on one or two observations;
  `payoff_5th_percentile` is a weak bound on a capped swap's downside, not a VaR.
- **For non-temperature weather products** (precipitation, snowfall, frost, wind) or
  for freight, emissions and other niche underlyings. The payoff and cap mechanics
  generalise; the index definitions here do not.
- **For basis risk between a hedge and a physical exposure.** A contract settles on
  one station's index, not on the hedger's load or revenue. Quantifying that residual
  is the job of `weather-data-signal-research-for-commodity-strategies`.

## Prerequisites

- Python 3.10+, standard library only (`math`, `dataclasses`, `enum`, `logging`,
  `datetime`).
- Daily station $(T_{\min}, T_{\max})$ observations, quality-controlled, with the
  temperature unit known — the engine never infers it.
- The contract specification: station, accumulation period, index type, multiplier and
  currency, strike, and any payout cap.

## Workflow

1. **Bind the contract to a verified specification.** Call
   `WeatherDerivativeContract.from_spec("CME_US_DEGREE_DAY", ...)` (or the European /
   Pacific Rim key) so `tick_value` and `currency` are taken from the published
   specification. Supply `tick_value` and `currency` by hand only for an OTC swap,
   where they are negotiated. For a FUTURES position also set `entry_index_price` —
   the index level the position was opened at.
2. **Accumulate the index.** Call `calculate_monthly_index(temps, index_type, unit,
   base_temperature=...)`. The base must be in the same unit as the observations
   (65 °F for US contracts, 18 °C for European HDD); CAT takes no base and requires
   Celsius. A non-finite or inverted observation raises — repair or explicitly infill
   the station series first, and record the infill, because it changes the index you
   will later reconcile against Speedwell.
3. **Settle.** For futures, `calculate_settlement_payoff` returns P&L,
   $(I_{\text{final}} - I_{\text{entry}}) \times M \times Q$; use
   `final_settlement_value` when you want the contract's cash settlement value
   $I \times M \times Q$ instead. Do not use one where the other is meant — that is
   the difference between a position's profit and its entire notional. Options return
   intrinsic value at expiry, before premium.
4. **Detrend before valuing.** Run `detrend_historical_indexes(history)` on a
   chronologically ordered record before burn analysis. It fits $I_j = a + bj$ by OLS
   and re-centres every season on the fitted level of the target season, preserving
   each season's departure from the fitted climate. If the fitted slope is large
   relative to the residual dispersion, treat the linear model as a decision point,
   not a default: check it against the station's documented history before relying on
   it, since a station relocation or instrument change produces the same slope as a
   climate trend and must be handled as a break, not a trend.
5. **Run burn analysis.** `run_burn_analysis(contract, detrended_history)` replays the
   contract against every season. Read `expected_payoff` as the fair value (pass
   `discount_factor` for anything but a short-dated contract) and
   `worst_historical_payoff` as the risk figure. For a short swap the worst realised
   payoff, not the mean, sizes the position.
6. **Cap and document OTC exposure.** Set `max_payout` (and `max_loss` if the cap is
   asymmetric) on every swap sold, and track counterparty mark-to-market against the
   ISDA credit support annex threshold.

## Common Pitfalls

- **Assuming a universal \$20 multiplier.** It is USD 20 per index point for CME *US*
  degree-day contracts only. European HDD and CAT are **EUR 20**; Pacific Rim (Tokyo)
  CAT is **JPY 2,500** — a 125× difference in the notional per point, on top of the
  currency error. `from_spec` exists so this value is never retyped from memory.
- **Reading a futures settlement value as P&L.** $I \times \$20$ is what the contract
  is worth at settlement; a position's profit is $(I_{\text{final}} -
  I_{\text{entry}}) \times \$20$. Confusing them overstates P&L by the entire entry
  notional — for an 880-entry contract settling at 900, \$18,000 instead of \$400.
- **Letting a missing observation score as zero.** `max(0.0, float('nan'))` evaluates
  to `0.0` in Python, so an unguarded NaN silently *lowers* an HDD index by a full
  day's degree days with no error anywhere. Missing station data must raise, not
  default.
- **Applying a 65 °F base to Celsius data.** Ten days at a 5 °C mean are 130 HDD
  against the European 18 °C base and 600 against a 65 °F base — a 4.6× error that
  produces no exception because both numbers are plausible degree-day totals.
- **Rejecting a negative CAT index.** HDD and CDD are sums of non-negative daily
  values, but a CAT index sums daily mean temperatures in Celsius and is legitimately
  negative over a cold accumulation period. A blanket non-negativity check rejects
  valid settlement data.
- **Black-Scholes on a weather underlying.** Weather is non-storable and non-tradable,
  so there is no replicating portfolio and no cost-of-carry drift; the index is also
  strongly mean-reverting and seasonal, which lognormal diffusion does not describe.
  Price by burn analysis or a stochastic temperature model.
- **Burn analysis on an undetrended record.** A raw 20–30 year mean sits at the
  midpoint of a warming record, overstating winter HDD and understating summer CDD.
  On a −10 HDD/season record the raw mean overprices an 800-strike call by 12.5%.
- **Selling an uncapped OTC weather swap.** Without a `max_payout`, a single extreme
  season is an unbounded loss. Note that a zero cap is a real zero cap: `max_payout=0.0`
  means the payoff is floored and capped at zero, while `None` means uncapped.
- **Treating one season's dispersion as risk.** A single-season sample has no sample
  standard deviation; reporting `0.0` reads as "no weather risk". The engine raises
  below two seasons.

## Verification

Run the unit test suite. It covers index accumulation under each unit and base,
per-venue multipliers and currencies, futures P&L versus settlement value, option
intrinsic value at and around the strike, symmetric and asymmetric caps, negative CAT
settlement, OLS detrending against hand-computed residuals, and burn-analysis
moments and tails against independently derived values:

```bash
python -m unittest discover -s skills/weather-derivatives-and-niche-instrument-handling/scripts
```

Then work through `assets/checklist.md` before trading or settling.

## Related Skills

- `weather-data-signal-research-for-commodity-strategies`
- `variance-swap-and-volatility-derivative-pricing`
- `warrants-and-structured-product-integration`
- `total-return-swap-synthetic-exposure`
- `commodity-futures-storage-and-carry-cost-modeling`
- `counterparty-credit-risk-for-otc-derivatives`
- `physical-vs-cash-settlement-handling`
