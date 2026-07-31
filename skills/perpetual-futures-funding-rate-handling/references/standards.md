# Standards for Perpetual Futures Funding Rate Handling

| Metric | Engineering Standard |
|---|---|
| Standard Funding Interval | 8-hour settlement intervals (3 payments per day). |
| Annualized APR Formula | $\text{APR} = F \cdot \left( \frac{365 \cdot 24}{\text{IntervalHours}} \right) \cdot 100\%$. |
| Funding Sign Rule | Positive rate $F > 0 \implies$ Longs pay Shorts. |