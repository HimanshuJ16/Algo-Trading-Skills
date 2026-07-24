# Broker & Framework Coverage — walk-forward-optimization-window-management

| Window Mode | In-Sample Behavior | Out-of-Sample Behavior | Recommended Use Case |
|---|---|---|---|
| Rolling WFO | Fixed-length sliding window (e.g. 1 year) | Fixed-length testing window (e.g. 3 months) | Fast-regime changing markets (Crypto, Forex) |
| Anchored WFO | Expanding window anchored to start date | Fixed-length testing window (e.g. 3 months) | Structural long-term equities / macro trend |

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with institutional quantitative validation, PBO (Probability of Backtest Overfitting) testing, and model risk management standards.
