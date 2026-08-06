# Standards for Strategy Correlation Matrix Live Recomputation

| Parameter | Standard Value | Description |
|---|---|---|
| High Correlation Alert | $\rho_{i,j} \ge 0.70$ | Triggers warning alert for pair correlation convergence. |
| Portfolio Avg Correlation | $\bar{\rho} \ge 0.55$ | Flags portfolio diversification compromise. |
| Shrinkage Factor | $\delta = 0.15$ | Ledoit-Wolf shrinkage weight towards target identity matrix. |