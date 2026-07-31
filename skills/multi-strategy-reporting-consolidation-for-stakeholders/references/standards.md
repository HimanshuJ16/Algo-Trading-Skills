# Standards for Multi-Strategy Reporting Consolidation

| Metric | Engineering Standard |
|---|---|
| Portfolio Volatility | $\sigma_p = \text{std}(R_{p, t}) \cdot \sqrt{252}$ from weighted daily return series. |
| Portfolio Sharpe Ratio | $SR_p = (R_p - R_f) / \sigma_p$. |
| Diversification Ratio | $\text{Ratio} = (\sum w_k \sigma_k) / \sigma_p \ge 1.0$. |