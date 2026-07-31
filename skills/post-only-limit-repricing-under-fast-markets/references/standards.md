# Standards for Post-Only Limit Repricing Under Fast Markets

| Metric | Engineering Standard |
|---|---|
| Reprice Boundary | BUY Post-Only MUST be $\le \text{best\_bid}$; SELL Post-Only MUST be $\ge \text{best\_ask}$. |
| Max Reprice Attempts | Maximum allowable consecutive reprices $\le 3$ per order cycle. |
| Tick Alignment | Repriced limit values MUST be rounded to exact exchange tick size. |