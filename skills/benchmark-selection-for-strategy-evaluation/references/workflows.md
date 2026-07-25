# Workflow: Benchmark Selection

1. **Identify Candidate Benchmarks**
   Gather historical returns for major indices (SPY, QQQ, IWM), sector ETFs (XLF, XLK), and the risk-free rate.

2. **Align Time Series**
   Ensure the strategy returns and benchmark returns are perfectly aligned by date.

3. **Compute Metrics**
   Use `BenchmarkSelector` to calculate Correlation, Tracking Error, and Information Ratio against all candidates.

4. **Select Appropriate Benchmark**
   Choose the benchmark with the highest correlation to the strategy. This isolates the strategy's true active return (alpha) rather than rewarding it for embedded beta exposure.
