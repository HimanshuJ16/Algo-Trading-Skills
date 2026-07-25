# Benchmarking Workflow

1. Aggregate the daily net returns of your multi-strategy portfolio.
2. Retrieve the daily returns of your policy benchmark (e.g., S&P 500, a blend of indices, or the Risk-Free rate for Absolute Return funds).
3. Instantiate `MultiStrategyBenchmarker`.
4. Call `evaluate_performance(portfolio_returns, benchmark_returns)`.
5. Review the resulting `Beta` and `Information Ratio` to determine if the strategy is generating true Alpha or simply replicating a leveraged ETF.