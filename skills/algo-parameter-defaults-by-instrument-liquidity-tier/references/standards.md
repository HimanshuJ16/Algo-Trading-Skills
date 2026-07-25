# Standards for Algo Parameter Defaults

| Liquidity Tier | Default Algo | Max Participation | Cross Spread? | Reasoning |
|---|---|---|---|---|
| **HIGH** | TWAP | 5% | Yes | Spreads are tight; market impact is low. |
| **MEDIUM** | VWAP | 10% | No | Follows the volume curve to blend in. |
| **LOW** | IS | 20% | No | Spreads are too wide; crossing guarantees immediate massive slippage. IS balances urgency vs price. |

## Category
`execution-algorithms`