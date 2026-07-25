# Standards for Illiquid Execution

| Order Size (% of ADV) | Liquidity Tier | Target Venue / Order Type | Reason |
|---|---|---|---|
| **< 1%** | Liquid | Continuous (VWAP) | Low risk of market impact. |
| **1% - 5%** | Moderate | Hybrid (VWAP + LOC) | Balance impact across the day and the close. |
| **> 5%** | Severe | 100% LOC Auction | Continuous trading would cause unacceptable slippage. |

*Note: MOC (Market-on-Close) is banned for Severe illiquidity due to the lack of price protection against auction imbalances.*

## Category
`execution-algorithms`
