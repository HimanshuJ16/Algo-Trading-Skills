# Pre-Flight Checklist

- [ ] Are GARCH parameters stationary ($\alpha + \beta < 1.0$)?
- [ ] Is block bootstrap using contiguous block size $\ge 5$ to preserve autocorrelation?
- [ ] Are synthetic return paths validated for volatility and moment parity against empirical baselines?
- [ ] Is random seed explicitly configured to ensure reproducible Monte Carlo backtest augmentation?
