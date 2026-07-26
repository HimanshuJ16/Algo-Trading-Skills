# Standards for Credit Card Data Signal Construction

| Metric | Engineering Standard |
|---|---|
| Availability Lag Handling | Backtesting MUST apply a minimum 3-day data availability lag to credit card panel feeds. |
| Consensus Source | Consensus revenue estimates MUST be sourced from point-in-time Bloomberg/FactSet consensus. |
| Signal Threshold | Directional beat/miss signals MUST require at least $\pm 2.5\%$ deviation from consensus to filter noise. |