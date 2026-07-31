# Standards for Hot/Cold Wallet Allocation

| Metric | Engineering Standard |
|---|---|
| Target Hot Ratio | Hot wallet balance SHOULD target 15% of total portfolio. |
| Max Hot Threshold | Sweep to Cold MUST be triggered if Hot Ratio $> 25\%$. |
| API Key Permissions | Bot trading API keys MUST NOT have withdrawal permissions (`withdraw=False`). |