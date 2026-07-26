# Pre-Flight Checklist

- [ ] Is model architecture selected (`CROSS_SECTIONAL` vs `TIME_SERIES`) based on strategy mandate?
- [ ] Are cross-sectional Z-scores standardized across the asset axis at each timestamp?
- [ ] Are cross-sectional portfolio weights verified for zero net dollar exposure ($\sum w_i = 0$)?
- [ ] Are time-series trend positions scaled inversely by realized volatility?
