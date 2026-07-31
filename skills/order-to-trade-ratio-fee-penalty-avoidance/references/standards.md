# Standards for Order-to-Trade Ratio Fee Penalty Avoidance

| Metric | Engineering Standard |
|---|---|
| Count OTR Formula | $\text{OTR}_{\text{count}} = \frac{\text{Orders} + \text{Cancels} + \text{Modifies}}{\max(1, \text{Trades})}$. |
| Defensive Warning Threshold | $80\%$ of exchange maximum threshold. |
| Excess Penalty Surcharge | $M_{\text{excess}} \times \text{FeePerExcessMessage}$ (e.g. $€0.05$/msg). |