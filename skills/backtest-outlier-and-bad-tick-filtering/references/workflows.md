# Deep Workflow Reference — backtest-outlier-and-bad-tick-filtering

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Compute Rolling Median & MAD**:
   - Compute rolling median and Median Absolute Deviation over a sliding window (e.g. 21 ticks).
2. **Evaluate Modified Z-Score**:
   - Compute $Z_i = \frac{0.6745 \cdot |P_i - \text{median}|}{\text{MAD}}$.
3. **Filter Outliers & Jumps**:
   - Purge non-positive prices, single-tick jumps $>20\%$, and MAD Z-scores $>5.0$.
4. **Generate Cleanliness Report**:
   - Output total raw ticks, bad ticks purged, and dataset cleanliness percentage.

## Production Implementation Reference

- Reference code: `scripts/outlier_filter.py` (`OutlierBadTickFilter`, `FilteredTickReport`).
- Automated unit tests: `scripts/test_outlier_filter.py`.
