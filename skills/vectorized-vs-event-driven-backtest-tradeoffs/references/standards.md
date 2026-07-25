# Backtesting Methodology Standards — vectorized-vs-event-driven-backtest-tradeoffs

| Engine Architecture | Computational Speed | Realism & Fill Accuracy | Use Case |
|---|---|---|---|
| Vectorized Matrix Engine | High ($1,000\times$ faster) | Idealized (Close/Open fills) | Parameter grid sweeps, alpha research |
| Event-Driven Engine | Moderate ($1\times$) | High (Order queue, partial fills) | Final strategy sign-off, live promotion |

## Category

`backtesting-methodology` — see top-level `mappings/` directory.
