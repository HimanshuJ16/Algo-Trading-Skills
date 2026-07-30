# Standards for Exchange Matching Engine Behavior Under Load

| Metric | Engineering Standard |
|---|---|
| Queuing Model Standard | Matching engine delay MUST be modeled using $M/M/1$ or $M/G/1$ queuing theory. |
| High Congestion Threshold | Utilization $\rho \ge 0.85$ MUST trigger `PAUSE_PASSIVE_QUOTING` to prevent sniping. |
| Spreads Widening Threshold | Utilization $0.50 \le \rho < 0.85$ MUST trigger `WIDEN_PASSIVE_SPREADS`. |