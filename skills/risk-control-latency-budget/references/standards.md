# Standards — risk-control-latency-budget

| Risk Control Type | Maximum SLA Budget | Action on Breach |
|---|---|---|
| HFT Pre-Trade Filter | $\le 5$ ms | Reject trade |
| Intraday Drawdown Breaker | $\le 50$ ms | Trigger circuit breaker alert |
| EOD Risk Reporting | $\le 5000$ ms | Flag queue congestion |

## Category

`risk-management`
