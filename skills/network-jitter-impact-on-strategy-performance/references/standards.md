# Standards for Network Jitter Impact on Strategy Performance

| Metric | Engineering Standard |
|---|---|
| Tail Jitter Metric | $P_{99}$ latency MUST be tracked alongside mean latency. |
| Sharpe Degradation Formula | $SR(\sigma_{\tau}) = \max(0.0, SR_{\text{base}} - \gamma \sigma_{\tau})$. |
| Max Jitter Threshold | $\sigma_{\max} = (SR_{\text{base}} - SR_{\text{min}}) / \gamma$. |
