# Standards for Adaptive Execution Under Volatility Spikes

| Metric | Threshold | Action | Rationale |
|---|---|---|---|
| **Normal Volatility** | Z-Score < 2.0 | Standard TWAP/VWAP | Market is operating efficiently. |
| **High Volatility** | 2.0 <= Z-Score < 5.0 | Reduce participation by 50%. Widen price bands. | Liquidity mirage risk. Tighter spreads are illusory; large orders will sweep the book. |
| **Critical Shock** | Z-Score >= 5.0 | Halt Execution | Prevents algorithmic feedback loops (Flash Crash amplification) and capital loss. |

*Note: Volatility z-scores should be computed against a trailing 20-period moving average.*