# Institutional Weather Signal Research Operations Checklist

## Meteorological Ingestion & Quality Control
- [ ] **NOAA / GFS / ECMWF Ingestion Verification**: Verify automated retrieval of 00z, 06z, 12z, and 18z ensemble model runs.
- [ ] **Station Data Cleaning**: Filter outlier temperature spikes and fill missing station data using spatial interpolation.
- [ ] **Base Temperature Calibration**: Confirm $65^\circ\text{F}$ for Energy (HDD/CDD) and $50^\circ\text{F}$ for Corn/Soybeans (GDD).

## Degree Day Aggregation & Anomaly Modeling
- [ ] **Population & Demand Weighting**: Apply census population weights ($w_i$) to station HDD/CDD calculations.
- [ ] **10-Year Baseline Norm Calculation**: Compute 10-year rolling mean ($\mu$) and standard deviation ($\sigma$) per day of year.
- [ ] **Z-Score Anomaly Triggering**: Verify $Z = \frac{X - \mu}{\sigma}$ triggers `LONG` at $Z \ge +1.5$ and `SHORT` at $Z \le -1.5$.

## Model Shift & Execution Controls
- [ ] **Ensemble Model Shift Tracking**: Compute 14-day cumulative GW-HDD delta ($\Delta \text{HDD} = \text{Run}_{12z} - \text{Run}_{00z}$).
- [ ] **Commodity Contract Mapping**: Map weather signals to liquid futures symbols (`NG`, `C`, `S`, `CL`).
- [ ] **EIA / USDA Report Freeze**: Freeze execution during Thursday EIA Natural Gas storage and monthly USDA WASDE report releases.