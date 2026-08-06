# Standards for Synthetic Data Generation for Backtest Augmentation

| Simulation Method | Formula / Principle | Standard Choice |
|---|---|---|
| GBM | $S_t = S_{t-1} e^{(\mu - \frac{1}{2}\sigma^2)dt + \sigma \sqrt{dt} Z_t}$ | Continuous diffusion baseline. |
| GARCH(1,1) | $\sigma_t^2 = \omega + \alpha \epsilon_{t-1}^2 + \beta \sigma_{t-1}^2$ | Volatility clustering ($\alpha + \beta < 1.0$). |
| Circular Block Bootstrap | Resample contiguous blocks $B$ | Block size $B=5$ days for daily returns. |
| Volatility Tolerance | $| \sigma_{\text{synth}} - \sigma_{\text{hist}} | / \sigma_{\text{hist}}$ | $\le 35\%$ difference threshold. |
