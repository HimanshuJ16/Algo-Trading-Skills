# Institutional Standards — monte-carlo-strategy-robustness-testing

| Simulation Mode | Sampling Method | Purpose | Acceptance Threshold |
|---|---|---|---|
| Sequence Shuffling | Without replacement | Sequence dependency & drawdowns | $DD_{95} \le 20\%$ |
| Bootstrap Resampling | With replacement | Regime variation & Sharpe stability | Risk of Ruin $\le 1.0\%$ |
| Price Noise Injection | Gaussian noise addition | Execution slippage sensitivity | Median return $> 0.0$ |

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with institutional model risk management (SR 11-7 guidelines), stress testing standards, and quantitative portfolio allocation controls.
