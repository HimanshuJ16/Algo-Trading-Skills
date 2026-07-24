# Financial ML Standards — regime-detection-for-strategy-switching

| Market Regime | Indicator Threshold | Active Strategy Variant |
|---|---|---|
| Bull Trending | $\text{ADX} \ge 25.0$, $+\text{DI} > -\text{DI}$ | `TrendFollowingLongStrategy` |
| Bear Trending | $\text{ADX} \ge 25.0$, $-\text{DI} > +\text{DI}$ | `TrendFollowingShortStrategy` |
| Mean-Reverting Ranging | $\text{ADX} < 20.0$ | `MeanReversionBollingerStrategy` |
| High Volatility Crash | ATR Z-Score $\ge 2.0\sigma$ | `RiskOffHaltStrategy` (De-leverage) |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with adaptive algorithmic trading, dynamic risk management, and automated strategy routing standards.
