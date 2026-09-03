---
name: weather-data-signal-research-for-commodity-strategies
description: >-
  Use when researching weather signals for energy or agricultural futures, computing
  population-weighted heating and cooling degree days and growing degree days from
  station or forecast data. Pricing weather derivatives is a separate skill.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: alt-data, weather-signals, degree-days, hdd, cdd, gdd, natural-gas, commodities, z-score
  brokers_frameworks: "cbot; nymex; ice; noaa; ecmwf; gfs"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when researching alternative weather datasets (NOAA, ECMWF, GFS ensemble forecasts), computing thermal degree day metrics, or building directional systematic signals for Energy futures (Natural Gas $NG$, Power $ERCOT/PJM$) and Agricultural futures (Corn $ZC$, Soybeans $ZS$).

This skill provides institutional mechanisms to:
- Compute **Heating Degree Days ($\text{HDD} = \max(0, 65^\circ\text{F} - T_{\text{mean}})$)** and **Cooling Degree Days ($\text{CDD} = \max(0, T_{\text{mean}} - 65^\circ\text{F})$)**.
- Compute **NOAA Modified Growing Degree Days** for agricultural crop development modeling, clamping $T_{\text{max}}$ at $86^\circ\text{F}$ and $T_{\text{min}}$ at $50^\circ\text{F}$ *before* averaging.
- Apply **Regional Population Weighting** ($\sum w_i \times \text{HDD}_i$) to align station temperatures with gas consumption centers.
- Derive **Strictly-Trailing Climate Anomaly Z-Scores** ($Z = \frac{X_{\text{forecast}} - \mu}{\sigma}$) from a seasonal window that structurally excludes the scored date and everything after it.
- Generate directional trading signals (`LONG`, `SHORT`, `NEUTRAL`) and confidence scores.

## When NOT to Use

- **Pricing or settling weather derivatives.** CME HDD/CDD/CAT futures and OTC weather swaps are priced by burn analysis against a $\$20$/index-point multiplier, not by anomaly Z-scores. Use `weather-derivatives-and-niche-instrument-handling`.
- **Sub-daily or intraday power load forecasting.** Degree days are a daily $(T_{\text{max}} + T_{\text{min}})/2$ construct and cannot resolve the hourly load shape that intraday ERCOT/PJM strategies trade.
- **Crop phenology or yield forecasting.** GDD accumulation models crop maturity timing; it is not a price-directional input, and this skill's signal mapping must not be fed raw GDD (see Pitfalls).
- **Any commodity whose price is not physically weather-driven** (metals, most financials). A statistically significant weather Z-score on such a contract is almost certainly a multiple-testing artifact.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `statistics`, `math`, `typing`).
- Daily weather station observations or GFS/ECMWF numerical model ensemble forecasts (min/max temperature, location, population weight).
- At least `lookback_years` of history for the *same* metric being scored, so a trailing climate norm can be estimated.

## Workflow

1. **Ingest Station Observations**: Construct `WeatherObservation` instances containing station ID, region, date, $T_{\text{min}}$, $T_{\text{max}}$, and weight $w_i$. Use census population (or gas/power consumption) weights for HDD/CDD; use harvested-acreage or production weights for GDD — population weighting a corn belt index is meaningless.
2. **Calculate Degree Days**: Call `calculate_degree_days(observations)` **one date at a time**. The helper rejects a mixed-date batch rather than silently collapsing several days into a single index. It also rejects duplicate station IDs, negative weights, transposed $T_{\text{max}}/T_{\text{min}}$, and non-finite temperatures — a NaN temperature would otherwise flow through to a NaN Z-score and surface as a *false* `NEUTRAL`.
3. **Build a Trailing Climate Norm**: Call `compute_climate_baseline(history, as_of=signal_date)` to derive $\mu$ and $\sigma$ from observations dated *strictly before* `as_of`, matched to the same seasonal window ($\pm$`day_window` calendar days, wrapping at year end). Do not compute $\mu$/$\sigma$ over a full series with pandas — that leaks the scored point into its own baseline. If too few observations qualify, the helper raises: widen the window or suppress the signal for that date, do not fall back to a thin baseline.
4. **Compute Climate Anomaly Z-Score**: Invoke `compute_anomaly_zscore(current_val, baseline_mean, baseline_std)`. The returned value is deliberately unrounded, because rounding before the threshold test promotes $Z = 1.496$ to $1.50$ and fires a trade at a threshold that was never met.
5. **Generate Directional Trade Signal**: Call `generate_commodity_trade_signal(sector, symbol, current_val, mean, std, date)`. All three statistics must describe the **same price-bullish-oriented metric** — HDD/CDD for Energy, a crop-*stress* metric (EDDI, soil-moisture deficit) for Agriculture. Signals with `direction == NEUTRAL` carry `confidence_score == 0.0` so a sizer keyed on confidence cannot size an untriggered call.
6. **Evaluate Model Shift**: Compare successive model runs on a like-for-like basis. GFS runs 00z/06z/12z/18z out to 16 days, so all four cycles support a 14-day cumulative delta; ECMWF's 06z/18z cycles only extend to 144 h, so ECMWF 14-day deltas must be computed 00z-vs-12z (see `references/workflows.md`).

## Common Pitfalls

- **Unweighted Station Aggregation**: Averaging temperatures across weather stations without population/consumption weights leads to false demand signals (e.g. treating a cold snap in low-population Montana equal to one in high-demand New York/Chicago).
- **Unclamped Growing Degree Days**: The NOAA/Barger *Modified* GDD standard caps $T_{\text{max}}$ at $86^\circ\text{F}$ and floors $T_{\text{min}}$ at $50^\circ\text{F}$ **before** averaging, because corn accrues no appreciable growth outside that band. A plain $(T_{\text{max}} + T_{\text{min}})/2 - 50$ overstates heat units on exactly the hot days that matter: a $90^\circ/72^\circ\text{F}$ day is 29 modified GDD, not 31.
- **Feeding GDD into the Directional Signal**: A high GDD anomaly means the crop is developing *faster*, which is neither bullish nor bearish on its own. Mapping it through the agricultural `LONG` on high-$Z$ rule inverts the intended drought trade. Agricultural direction requires a stress-oriented metric (EDDI, soil-moisture deficit, heat-stress degree days above the crop optimum); GDD belongs in maturity modeling.
- **Ignoring Model Forecast Shifts**: Natural Gas futures typically re-price around ECMWF/GFS run releases (00z, 06z, 12z, 18z). Trading historical actuals without tracking model forecast shifts results in lag.
- **Assuming Every Model Cycle Has the Same Horizon**: ECMWF's 06z and 18z HRES/ENS cycles run only to 90 h / 144 h; only the 00z and 12z cycles reach the medium range. Differencing a 14-day cumulative HDD across ECMWF 00z and 06z compares a full forecast against a truncated one and manufactures a phantom revision.
- **Lookahead Bias in Climate Norms**: Calculating baseline $\mu$ and $\sigma$ using future data points introduces severe backtest overfitting. Baselines must be expanding or strictly rolling historical windows — `compute_climate_baseline` enforces this by construction.
- **Seasonal Base Temperature Misconfiguration**: Using $65^\circ\text{F}$ base temperature for agricultural crops instead of species-specific base temperatures ($50^\circ\text{F}$ for corn) invalidates crop growth modeling.
- **Trading Legacy Open-Outcry Symbols**: `C` and `S` are the retired CBOT floor codes for Corn and Soybeans. CME Globex — and therefore every electronic broker API — uses `ZC` and `ZS`. Routing on `C`/`S` will be rejected or, worse, matched to a different product.

## Verification

Run the unit test suite to validate HDD/CDD/Modified-GDD degree day math, weighted aggregation, input validation, look-ahead-safe baselines, threshold boundary behavior, and commodity trade signal mapping:

```bash
python -m unittest discover -s skills/weather-data-signal-research-for-commodity-strategies/scripts
python tools/validate_skills.py
```

Verify against `assets/checklist.md` before promoting any weather signal to live capital.

## Related Skills

- `weather-derivatives-and-niche-instrument-handling`
- `lookahead-bias-elimination`
- `global-macro-economic-calendar-integration`
- `web-scraped-sentiment-data-pipeline`
- `transfer-learning-across-correlated-instruments`
