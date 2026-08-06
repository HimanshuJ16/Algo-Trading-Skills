# Standards for Strategy Research-to-Production Pipeline Governance

| Governance Gate | Standard Threshold |
|---|---|
| Backtest Sharpe Ratio | $\ge 1.50$ (Out-of-sample). |
| Shadow Tracking Error | $\le 5.0\%$ between paper trading fills & simulated fills. |
| Shadow Paper Trading Duration | $\ge 14$ consecutive calendar days. |
| Reproducibility | 100% mandatory Git commit hash & dataset checksum. |
| Production Gatekeeper | Independent Risk Committee sign-off MANDATORY. |