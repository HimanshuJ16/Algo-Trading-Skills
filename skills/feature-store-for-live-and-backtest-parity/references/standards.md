# Financial ML Standards — feature-store-for-live-and-backtest-parity

| Mode | Input Source | Calculation Architecture | Maximum Tolerance ($\epsilon$) |
|---|---|---|---|
| Offline Batch | Historical Bar Matrix | Vectorized window slicing | Baseline |
| Online Streaming | Live Tick / Bar Stream | Rolling Ring Buffer ($N=\text{lookback}$) | $\le 1 \times 10^{-6}$ |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with ML model governance (SR 11-7), train-test skew prevention, and institutional quantitative feature engineering standards.
