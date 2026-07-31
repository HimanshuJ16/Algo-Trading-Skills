# Standards for Portfolio Construction with Transaction Cost Awareness

| Metric | Engineering Standard |
|---|---|
| Buffer Band Threshold | Standard rebalancing buffer $\ge 2.0\%$ weight shift. |
| Transaction Cost Model | $\text{TC} = (c_{\text{comm}} + c_{\text{spread}}) \cdot |\Delta w| + c_{\text{impact}} \cdot (\Delta w)^2$. |
| Max Turnover Limit | Single rebalance turnover MUST NOT exceed $50.0\%$. |