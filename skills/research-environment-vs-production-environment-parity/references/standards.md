# Standards for Research Environment vs Production Environment Parity

| Metric | Engineering Standard |
|---|---|
| Signal Skew Tolerance | Live production signals MUST NOT deviate by > $0.1\%$ from research backtest signals. |
| Feature Hash Identity | Code hashes of feature generation pipelines MUST be 100% identical. |
| Precision Parity | Floating-point precision MUST match (`float64` across both environments). |