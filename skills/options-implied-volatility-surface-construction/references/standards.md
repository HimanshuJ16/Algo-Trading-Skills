# Standards for Options Implied Volatility Surface Construction

| Metric | Engineering Standard |
|---|---|
| Total Implied Variance Formula | $w(k, \tau) = \sigma^2 \tau$. |
| Calendar Spread Arbitrage Rule | Total implied variance MUST be non-decreasing with expiration ($\frac{\partial w}{\partial \tau} \ge 0$). |
| Minimum Implied Volatility | Absolute floor $\sigma \ge 0.05$ ($5\%$). |
