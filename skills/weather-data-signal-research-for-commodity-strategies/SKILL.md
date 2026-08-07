---
name: weather-data-signal-research-for-commodity-strategies
description: "Institutional alternative data skill for researching weather signals, calculating population-weighted Heating Degree Days (HDD), Cooling Degree Days (CDD), and Growing Degree Days (GDD), computing 10-year climate anomaly Z-scores, and generating directional trading signals for Energy (Natural Gas, Power) and Agricultural futures (Corn, Soybeans)."
domain: Quantitative Research & Alternative Data
subdomain: Meteorological & Commodity Signal Research
tags:
- alt-data
- weather-signals
- degree-days
- hdd
- cdd
- gdd
- natural-gas
- commodities
- z-score
brokers_frameworks:
- cbot
- nymex
- ice
- noaa
- ecmwf
- gfs
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when researching alternative weather datasets (NOAA, ECMWF, GFS ensemble forecasts), computing thermal degree day metrics, or building directional systematic signals for Energy futures (Natural Gas $NG$, Power $ERCOT/PJM$) and Agricultural futures (Corn $C$, Soybeans $S$).

This skill provides institutional mechanisms to:
- Compute **Heating Degree Days ($\text{HDD} = \max(0, 65^\circ\text{F} - T_{\text{mean}})$)** and **Cooling Degree Days ($\text{CDD} = \max(0, T_{\text{mean}} - 65^\circ\text{F})$)**.
- Compute **Growing Degree Days ($\text{GDD} = \max(0, T_{\text{mean}} - 50^\circ\text{F})$)** for agricultural crop development modeling.
- Apply **Regional Population Weighting** ($\sum w_i \times \text{HDD}_i$) to align station temperatures with gas consumption centers.
- Derive **10-Year Historical Climate Anomaly Z-Scores** ($Z = \frac{\text{HDD}_{\text{forecast}} - \mu}{\sigma}$).
- Generate directional trading signals (`LONG`, `SHORT`, `NEUTRAL`) and confidence scores.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `math`, `typing`).
- Daily weather station observations or GFS/ECMWF numerical model ensemble forecasts (min/max temperature, location, population weight).

## Workflow

1. **Ingest Station Observations**: Construct `WeatherObservation` instances containing station ID, region, date, $T_{\text{min}}$, $T_{\text{max}}$, and population weight $w_i$.
2. **Calculate Degree Days**: Call `calculate_degree_days(observations)` to compute raw and population-weighted HDD, CDD, and GDD metrics.
3. **Compute Climate Anomaly Z-Score**: Invoke `compute_anomaly_zscore(current_val, baseline_mean, baseline_std)` to evaluate standard deviation shifts from 10-year historical climate norms.
4. **Generate Directional Trade Signal**: Call `generate_commodity_trade_signal(sector, symbol, current_val, mean, std, date)` to output long/short signals for Energy or Agricultural commodities.
5. **Evaluate Model Shift**: Compare 00z vs 12z GFS/ECMWF model updates to trade intraday forecast shifts.

## Common Pitfalls

- **Unweighted Station Aggregation**: Averaging temperatures across weather stations without population/consumption weights leads to false demand signals (e.g., treating a cold snap in low-population Montana equal to one in high-demand New York/Chicago).
- **Ignoring Model Forecast Shifts**: Settlement prices for Natural Gas futures re-price on ECMWF/GFS ensemble updates (00z, 06z, 12z, 18z). Trading historical actuals without tracking model forecast shifts results in lag.
- **Lookahead Bias in Climate Norms**: Calculating historical baseline means ($\mu$) and standard deviations ($\sigma$) using future data points introduces severe backtest overfitting. Baselines must be expanding or strictly rolling historical windows.
- **Seasonal Base Temperature Misconfiguration**: Using $65^\circ\text{F}$ base temperature for agricultural crops instead of species-specific base temperatures ($50^\circ\text{F}$ for corn) invalidates crop growth modeling.

## Verification

Run the unit test suite to validate HDD/CDD/GDD degree day math, population weighting, baseline anomaly Z-scores, and commodity trade signal mapping:

```bash
python -m unittest discover -s skills/weather-data-signal-research-for-commodity-strategies/scripts
```

## Related Skills

- `weather-derivatives-and-niche-instrument-handling`
- `web-scraped-sentiment-data-pipeline`
- `transfer-learning-across-correlated-instruments`
- `tick-size-pilot-program-impact-assessment`
