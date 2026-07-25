# Standards for Adverse Selection Measurement

| Metric | Calculation | Industry Standard / Target |
|---|---|---|
| **Markout Units** | Basis Points (bps) | Used universally to normalize across different asset prices. |
| **Markout (Buy)** | `(Future_Mid / Fill_Price - 1) * 10000` | $> 0$ bps |
| **Markout (Sell)** | `(Fill_Price / Future_Mid - 1) * 10000` | $> 0$ bps |
| **Toxic Flow** | Majority of horizons are negative | Indicates algorithm is serving as a "free option" for informed flow. |

## Category
`execution-quality`
