# Standards for Cross-Asset Correlation Regime Shifts

| Metric | Engineering Standard |
|---|---|
| Frobenius Distance Threshold | Frobenius matrix distance $D_F > 0.80$ MUST trigger `CRISIS_CONVERGENCE` regime alert. |
| Window Ratio Standard | Baseline window MUST be at least 5x the short-term window (e.g. 100 days vs 20 days). |
| Dynamic Leverage Scaling | Multi-asset risk-parity leverage MUST be downscaled by 50% during `CRISIS_CONVERGENCE`. |
