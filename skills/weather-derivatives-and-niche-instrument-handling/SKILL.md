---
name: weather-derivatives-and-niche-instrument-handling
description: "Institutional exotics skill for pricing and settling CME Weather Derivatives (HDD, CDD, CAT futures & options) and OTC niche instruments (capped weather swaps), executing Burn Analysis historical simulation, and applying the standard $20 per index point tick multiplier."
domain: Multi-Asset Derivatives & Exotic Instruments
subdomain: Weather Derivatives & Niche Instrument Pricing
tags:
- weather-derivatives
- cme-weather
- hdd-futures
- cdd-futures
- burn-analysis
- otc-swaps
- tick-multiplier
- niche-instruments
brokers_frameworks:
- cme
- ice
- noaa
- otc-isda
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when trading, pricing, or settling CME Weather Futures/Options ($HDD, CDD, CAT$) or structuring Over-the-Counter (OTC) weather derivative swaps and niche environmental instruments.

This skill provides institutional mechanisms to:
- Accumulate monthly **Heating Degree Day (HDD)**, **Cooling Degree Day (CDD)**, and **Cumulative Average Temperature (CAT)** indexes from daily temperature observations.
- Value CME Weather Futures and Call/Put Options using the standard **\$20 per index point tick multiplier**.
- Structure and price **Capped OTC Weather Swaps** with maximum payout cap limits ($C_{\text{cap}}$).
- Execute **Burn Analysis (Historical Simulation)** across 20-30 years of meteorological station data to derive fair expected payoffs and risk metrics.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `math`, `typing`).
- Daily weather station temperature logs (minimum and maximum daily temperatures in Fahrenheit or Celsius).

## Workflow

1. **Define Contract Parameters**: Construct `WeatherDerivativeContract` specifying symbol, location, index type (`HDD`, `CDD`, `CAT`), instrument type (`FUTURES`, `CALL_OPTION`, `PUT_OPTION`, `CAPPED_SWAP`), strike index, tick multiplier ($\$20.00$), and optional payout cap limit.
2. **Accumulate Monthly Index**: Call `calculate_monthly_index(daily_min_max_temps, index_type)` to compute total degree day points for the contract period.
3. **Calculate Settlement Payoff**: Invoke `calculate_settlement_payoff(contract, accumulated_index)` to compute the cash settlement payoff scaled by $\$20/\text{point}$ and enforce payout caps.
4. **Execute Burn Analysis Valuation**: Call `run_burn_analysis(contract, historical_season_indexes)` to evaluate historical expected payoffs, standard deviations, and maximum historical drawdowns.
5. **Manage Counterparty Risk**: Track credit exposure limits on OTC weather swaps with ISDA documentation.

## Common Pitfalls

- **Ignoring the \$20 Multiplier**: CME Weather Futures and Options use a **\$20 multiplier per index point** ($1\ \text{HDD point} = \$20.00$). Sizing positions without the $\$20$ factor leads to a 20x error in settlement PnL math.
- **Black-Scholes Model Misapplication**: Weather is non-storable and non-tradable directly, rendering standard Black-Scholes delta hedging invalid. Weather derivatives must be priced via **Burn Analysis** or stochastic weather simulation models.
- **Neglecting Climate Warming Trends**: Performing Burn Analysis on unadjusted historical data (e.g. 1980-2020) overstates winter HDD indexes and understates summer CDD indexes due to long-term climate warming trends.
- **Un-Capped OTC Swap Risk**: Selling OTC weather swaps without a maximum payout cap ($C_{\text{cap}}$) exposes the firm to uncapped losses during extreme 100-year weather events.

## Verification

Run the unit test suite to validate monthly index accumulation, CME futures/options settlement math with $\$20/\text{point}$ multipliers, capped OTC swap payoffs, and Burn Analysis historical simulations:

```bash
python -m unittest discover -s skills/weather-derivatives-and-niche-instrument-handling/scripts
```

## Related Skills

- `weather-data-signal-research-for-commodity-strategies`
- `variance-swap-and-volatility-derivative-pricing`
- `warrants-and-structured-product-integration`
- `total-return-swap-synthetic-exposure`

