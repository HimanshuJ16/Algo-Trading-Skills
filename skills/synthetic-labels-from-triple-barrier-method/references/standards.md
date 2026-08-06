# Standards for Synthetic Labels from Triple Barrier Method

| Barrier Type | Multiplier / Window | Label Code |
|---|---|---|
| Take-Profit Barrier | $U_t = P_t (1 + pt \cdot \sigma_t)$ (default $pt = 2.0$) | $+1$ |
| Stop-Loss Barrier | $L_t = P_t (1 - sl \cdot \sigma_t)$ (default $sl = 1.0$) | $-1$ |
| Vertical Time-Out Barrier | Expiration horizon (default 10 bars) | $0$ |
| Dynamic Volatility Span | 20-period Exponentially Weighted Moving Std | N/A |
