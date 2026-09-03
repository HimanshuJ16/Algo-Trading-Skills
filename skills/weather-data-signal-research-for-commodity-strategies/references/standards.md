# Institutional Weather Data Signal Research Standards

## 1. Commodity & Weather Signal Correlation Matrix
| Commodity Futures | Primary Weather Metric | Base Temperature | Key Agricultural / Demand Region | Primary Driver |
| :--- | :--- | :--- | :--- | :--- |
| **Natural Gas (Henry Hub / NG)** | Heating Degree Days (HDD) | $65^\circ\text{F}\ (18.3^\circ\text{C})$ | US East & Midwest (Pop-Weighted) | Winter Heating Demand |
| **Electricity (ERCOT / PJM / CAISO)** | Cooling Degree Days (CDD) | $65^\circ\text{F}\ (18.3^\circ\text{C})$ | Texas, California, Mid-Atlantic | Summer Air Conditioning Peak |
| **Corn Futures (CBOT / ZC)** | Modified Growing Degree Days (GDD) | $50^\circ\text{F}\ (10.0^\circ\text{C})$, clamp band $50$–$86^\circ\text{F}$ | US Corn Belt (Iowa, Illinois) | Crop Thermal Maturity |
| **Soybean Futures (CBOT / ZS)** | EDDI / Soil Moisture Deficit | $50^\circ\text{F}\ (10.0^\circ\text{C})$, clamp band $50$–$86^\circ\text{F}$ | US Midwest & Brazil (Mato Grosso) | Pod-Filling Yield Stress |

**Contract symbols.** `ZC` (Corn) and `ZS` (Soybeans) are the CME Globex product codes and are what broker APIs expect. The single-letter `C` and `S` codes are retired open-outcry floor symbols; do not route on them. Corn and Soybean futures are each 5,000 bushels, listed on CBOT.

**Metric orientation.** The directional signal mapping in `scripts/` assumes a *price-bullish-oriented* input: higher metric implies a tighter physical balance. HDD and CDD satisfy this for Energy. GDD does **not** satisfy it for Agriculture — it measures development speed, not stress — so the agricultural mapping requires EDDI, a soil-moisture deficit, or heat-stress degree days accumulated above the crop optimum.

---

## 2. Quantitative Degree Day & Anomaly Equations

### A. Heating Degree Days (HDD):
$$\text{HDD} = \max\left(0,\; 65^\circ\text{F} - \frac{T_{\text{max}} + T_{\text{min}}}{2}\right)$$

### B. Cooling Degree Days (CDD):
$$\text{CDD} = \max\left(0,\; \frac{T_{\text{max}} + T_{\text{min}}}{2} - 65^\circ\text{F}\right)$$

NOAA CPC: "Heating degree days are summations of negative differences between the mean daily temperature and the 65°F base"; cooling degree days are "summations of positive differences from the same base."

### C. Modified Growing Degree Days (GDD):
$$\text{GDD} = \max\left(0,\; \frac{\min(T_{\text{max}},\,86^\circ\text{F}) + \max(T_{\text{min}},\,50^\circ\text{F})}{2} - 50^\circ\text{F}\right)$$

NOAA CPC: "Minimum temperatures less than 50°F are set to 50, and maximum temperatures greater than 86°F are set to 86," because "no appreciable growth is detected with temperatures lower than 50 or greater than 86." Barger (1969) proposed this Modified GDD form as the NOAA standard heat-unit formula. Worked examples: $80/55 \rightarrow 17.5$; $90/72 \rightarrow (86+72)/2 - 50 = 29$; $68/41 \rightarrow (68+50)/2 - 50 = 9$.

### D. Population-Weighted Regional Aggregation ($HDD_{\text{region}}$):
$$\text{HDD}_{\text{region}} = \sum_{i=1}^{M} w_i \times \text{HDD}_i \quad \text{where} \ \sum_{i=1}^{M} w_i = 1.0$$

NOAA CPC weights climate-division degree days "according to their proportion of the State's population" before rolling up to regional and national figures. Apply the analogous construction with harvested-acreage or production weights for agricultural indices.

### E. Climate Anomaly Z-Score ($Z_{\text{weather}}$):
$$Z_{\text{weather}} = \frac{X_{\text{forecast}} - \mu_{\text{baseline, 10yr}}}{\sigma_{\text{baseline, 10yr}}}$$

Where $X_{\text{forecast}}$ is the model forecast metric (e.g. 14-day cumulative HDD), $\mu$ is the 10-year seasonal-window mean, and $\sigma$ the sample standard deviation (ddof $= 1$). Both moments must be estimated from observations dated strictly before the scored date.

---

## 3. Signal Decision Standard Matrix
- **If $Z_{\text{weather}} \ge +1.5$ (Energy)**: Implying severe cold/heat snap $\rightarrow$ **LONG Futures / Call Options**.
- **If $Z_{\text{weather}} \le -1.5$ (Energy)**: Implying mild weather $\rightarrow$ **SHORT Futures / Put Options**.
- **If $Z_{\text{weather}} \ge +1.5$ (Agricultural, stress metric)**: Implying crop stress/drought $\rightarrow$ **LONG Futures**.
- **If $Z_{\text{weather}} \le -1.5$ (Agricultural, stress metric)**: Implying benign conditions $\rightarrow$ **SHORT Futures**.

Thresholds are inclusive at the boundary and are evaluated on the unrounded $Z$.

---

## 4. Numerical Model Run Cycles & Horizons
| Model | Cycles (UTC) | Horizon | Medium-range availability |
| :--- | :--- | :--- | :--- |
| **GFS (NCEP)** | 00z, 06z, 12z, 18z | 16 days (384 h), all four cycles | All four cycles support a 14-day cumulative index |
| **ECMWF HRES / ENS** | 00z, 06z, 12z, 18z | 00z & 12z reach the medium range; 06z & 18z run only to 90 h (HRES) / 144 h (ENS) | **Only 00z and 12z** support a 14-day cumulative index |

Differencing a 14-day cumulative HDD across an ECMWF 00z and 06z run compares a full forecast against a truncated one. Compute ECMWF run-to-run deltas 00z-vs-12z (or 12z-vs-prior-00z) only.

---

## 5. Data Source Characteristics & Release Timing
| Source | Publisher | Cadence / Latency | Signal implication |
| :--- | :--- | :--- | :--- |
| **NOAA CPC degree days** | NOAA Climate Prediction Center | Weekly & monthly summaries, population-weighted | Baseline/actuals, not real-time forecast input |
| **EDDI** (Evaporative Demand Drought Index) | NOAA Physical Sciences Laboratory | Daily, **experimental**, with a ~5-day lag; 1-week to 12-month timescales | Lagged nowcast — cannot be used as a same-day forecast input; align backtests to the lagged availability date |
| **EIA Weekly Natural Gas Storage Report** | US EIA | Thursdays, **10:30 a.m. ET** (holiday weeks shift; EIA publishes exceptions on its schedule page) | Freeze weather-driven NG execution across the print |
| **USDA WASDE** | USDA Office of the Chief Economist | Monthly, **12:00 noon ET** | Freeze weather-driven grain/oilseed execution across the print |

---

## 6. Source References
- NOAA Climate Prediction Center — Growing Degree Day explanation (base 50°F, 86°F cap, 50°F floor): <https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/gdd.shtml>
- NOAA Climate Prediction Center — Weekly & Monthly Degree Day Summaries explanation (65°F HDD/CDD base, population weighting): <https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/ddayexp.shtml>
- NOAA Physical Sciences Laboratory — EDDI (definition, experimental status, ~5-day lag): <https://psl.noaa.gov/eddi/>
- NCEP EMC — Global Forecast System (four cycles per day, 16-day horizon): <https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/gfs.php>
- ECMWF — Atmospheric model Ensemble 15-day forecast (Set III, ENS cycle horizons): <https://www.ecmwf.int/en/forecasts/datasets/set-iii>
- ECMWF — IFS Medium-range Control forecast and Analysis Data (Set I, HRES cycle horizons): <https://www.ecmwf.int/en/forecasts/datasets/set-i>
- US EIA — Weekly Natural Gas Storage Report schedule (Thursday 10:30 a.m. ET): <https://ir.eia.gov/ngs/schedule.html>
- USDA Office of the Chief Economist — WASDE report (monthly, 12:00 p.m. ET): <https://www.usda.gov/about-usda/general-information/staff-offices/office-chief-economist/commodity-markets/wasde-report>
- CME Group — Corn futures contract specifications (Globex code `ZC`): <https://www.cmegroup.com/markets/agriculture/grains/corn/specs>
- CME Group — Soybean futures contract specifications (Globex code `ZS`): <https://www.cmegroup.com/markets/agriculture/oilseeds/soybean.contractSpecs.html>
