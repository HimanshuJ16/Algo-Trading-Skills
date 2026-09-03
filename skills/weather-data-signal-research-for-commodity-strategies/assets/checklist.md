# Institutional Weather Signal Research Operations Checklist

## Meteorological Ingestion & Quality Control
- [ ] **NOAA / GFS / ECMWF Ingestion Verification**: Verify automated retrieval of the model cycles actually used. GFS publishes 00z/06z/12z/18z to 16 days; ECMWF reaches the medium range only on 00z and 12z (06z/18z stop at 90 h HRES / 144 h ENS).
- [ ] **Station Data Cleaning**: Filter outlier temperature spikes and fill missing station data using spatial interpolation. Confirm the pipeline rejects, rather than silently propagates, non-finite temperatures — a NaN yields a NaN Z-score that masquerades as a deliberate `NEUTRAL`.
- [ ] **Station Integrity Checks**: Assert one calendar date per aggregation batch, unique station IDs (a duplicate double-counts its weight), non-negative weights, and $T_{\text{max}} \ge T_{\text{min}}$.
- [ ] **Base Temperature Calibration**: Confirm $65^\circ\text{F}$ for Energy (HDD/CDD) and $50^\circ\text{F}$ for Corn/Soybeans (GDD).
- [ ] **Modified GDD Clamp Band**: Confirm $T_{\text{max}}$ is capped at $86^\circ\text{F}$ and $T_{\text{min}}$ floored at $50^\circ\text{F}$ *before* averaging (NOAA CPC / Barger 1969). Spot-check: $90/72^\circ\text{F} \rightarrow 29$ GDD, not 31.

## Degree Day Aggregation & Anomaly Modeling
- [ ] **Weight Vector Matches the Metric**: Census population (or gas/power consumption) weights for HDD/CDD; harvested-acreage or production weights for GDD. A population-weighted corn belt index is meaningless.
- [ ] **Trailing Baseline Norm Calculation**: Compute the 10-year mean ($\mu$) and **sample** standard deviation ($\sigma$, ddof $=1$) per seasonal window, from observations dated **strictly before** the scored date. Confirm no full-series `mean()`/`std()` is used anywhere in the research or backtest path.
- [ ] **Minimum Sample Enforcement**: Confirm the baseline raises (and the signal is suppressed) when too few observations fall in the window, rather than silently returning a thin or zero-dispersion norm.
- [ ] **Z-Score Anomaly Triggering**: Verify $Z = \frac{X - \mu}{\sigma}$ triggers `LONG` at $Z \ge +1.5$ and `SHORT` at $Z \le -1.5$, evaluated on the **unrounded** $Z$ — rounding first promotes $Z = 1.496$ to $1.50$ and fires an untriggered trade.
- [ ] **Metric Orientation Audit**: Confirm the metric fed to the signal mapping is price-bullish-oriented — HDD/CDD for Energy, a crop-*stress* metric (EDDI, soil-moisture deficit) for Agriculture. Raw GDD must not be routed through the directional mapping.
- [ ] **Alt-Data Availability Lag**: Align every backtest to the metric's real publication lag. EDDI is experimental and published with a ~5-day lag, so a same-day EDDI reading is not tradeable information.
- [ ] **Neutral Confidence Handling**: Confirm any position sizer keyed on `confidence_score` cannot size a `NEUTRAL` signal (confidence is 0.0 by construction).

## Model Shift & Execution Controls
- [ ] **Like-for-Like Run Comparison**: Compute run-to-run cumulative deltas only between runs of the same model covering the same horizon (ECMWF 00z-vs-12z; any GFS pair). Never difference GFS against ECMWF and call it a revision.
- [ ] **Revision Threshold Calibration**: Confirm the $|\Delta \text{HDD}|$ trigger is fitted on trailing data by region and season, and re-fit on a documented cadence — not hard-coded.
- [ ] **Commodity Contract Mapping**: Map weather signals to CME Globex product codes (`NG`, `ZC`, `ZS`, `CL`). The legacy floor codes `C` and `S` are retired and will not route.
- [ ] **EIA / USDA Report Freeze**: Freeze execution across the EIA Weekly Natural Gas Storage Report (Thursday 10:30 a.m. ET, subject to holiday shifts) and the monthly USDA WASDE (12:00 noon ET). Read both dates from the publisher's calendar rather than assuming a fixed weekday.
