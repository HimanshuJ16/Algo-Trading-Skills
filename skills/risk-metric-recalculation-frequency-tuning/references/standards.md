# Standards for Risk Metric Recalculation Frequency Tuning

| Metric Tier | Base Interval | Accelerated Interval | Target Risk Control |
|---|---|---|---|
| Tier 1 (Tick) | 0.0s | 0.0s | Real-Time Drawdown & Position Caps |
| Tier 2 (Fast) | 2.0s | 0.5s | Option Greeks Delta & Gamma |
| Tier 3 (Medium) | 30.0s | 5.0s | 1-Day Parametric VaR / CVaR |
| Tier 4 (Slow) | 300.0s | 30.0s | Portfolio Stress Scenario Testing |
