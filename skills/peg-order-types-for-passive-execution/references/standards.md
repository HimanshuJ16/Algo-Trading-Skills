# Standards for Peg Order Types for Passive Execution

| Metric | Engineering Standard |
|---|---|
| Midpoint Peg Price Formula | $P_{\text{mid}} = \frac{P_{\text{bid}} + P_{\text{ask}}}{2.0} + \text{offset}$. |
| Primary Peg BUY Reference | $P_{\text{ref}} = P_{\text{bid}} + \text{offset}$. |
| Limit Cap Protection Rule | BUY price MUST NOT exceed $P_{\text{limit\_cap}}$; SELL price MUST NOT drop below $P_{\text{limit\_cap}}$. |