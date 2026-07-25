# Standards for Cross-Margin Operations

| Metric | Engineering Standard |
|---|---|
| Isolated Margin | Sum of absolute base margins ($IM = \sum \|M_i\|$). |
| Cross Margin | $CM = IM - \sum (\text{Offsets})$. |
| Capital Efficiency Ratio | $CER = \frac{Isolated Margin}{Cross Margin}$. Higher is better. |
| Correlation Haircut | Never trust raw historical correlation. Always apply a 20-30% haircut to account for correlation breakdown in tail events. |