# Standards for Convertible Bond Arbitrage

| Metric | Engineering Standard |
|---|---|
| Parity Precision | Parity MUST be updated in real time on every underlying stock tick. |
| Borrow Rate Factoring | Stock borrow rate MUST be dynamically factored into net carry calculations before order placement. |
| Delta Hedging Cadence | Delta hedge ratio MUST be rebalanced when delta drift exceeds $\pm 5\%$. |