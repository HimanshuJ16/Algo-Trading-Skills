# Standards for Participation of Volume (POV) Execution

| Metric | Engineering Standard |
|---|---|
| Target Slice Formula | $Q_{\text{slice}} = \lfloor \frac{\text{TargetRate}}{1 - \text{TargetRate}} \times V_{\text{market}} \rfloor$. |
| Max Participation Cap | Standard limit MUST NOT exceed $30\%$ of total market volume. |
| Realized Rate Formula | $\text{RealizedRate} = \frac{Q_{\text{algo\_cum}}}{V_{\text{market\_cum}} + Q_{\text{algo\_cum}}}$. |