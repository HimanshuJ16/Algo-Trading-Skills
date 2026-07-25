# Backtesting Methodology Standards — multi-year-regime-coverage-requirement

| Requirement Metric | Recommended Value | Action on Breach |
|---|---|---|
| Minimum Backtest Duration | $\ge 3.0$ Years (750 bars) | Reject backtest (`Insufficient Duration`) |
| Minimum Regimes Covered | $\ge 3$ Regimes | Reject backtest (`Insufficient Regimes`) |
| Max Single-Regime Drawdown | $\le 25.0\%$ | Trigger `REGIME VETO` |

## Category

`backtesting-methodology` — see top-level `mappings/` directory.
