# Workflows for Synthetic Data Generation for Backtest Augmentation

1. **Parameter Estimation / Data Ingestion**:
   - Estimate drift ($\mu$), volatility ($\sigma$), or GARCH parameters ($\omega, \alpha, \beta$) from historical returns.
2. **Path Simulation**:
   - Generate synthetic price/return series using GBM, GARCH, or Block Bootstrap.
3. **Statistical Validation**:
   - Audit mean return, volatility, skewness, and kurtosis vs empirical distributions.
4. **Backtest Augmentation Integration**:
   - Inject synthetic paths into strategy backtester to measure Sharpe stability across 1,000 Monte Carlo paths.
