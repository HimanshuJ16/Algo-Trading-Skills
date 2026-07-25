# ML Backtesting TCA Workflow

1. Extract the raw continuous predictions (probabilities, expected returns, or Z-scores) from your ML pipeline.
2. Align predictions strictly with the *next period's* actual return (accounting for any execution delay).
3. Instantiate `MlTcaBacktester` with the appropriate `bps_cost` (basis points per half-turn).
4. Sweep `signal_threshold` to find the optimal hurdle rate where the model only trades when expected profit > execution cost.
5. Analyze `gross_cagr`, `net_cagr`, and `annual_turnover` to evaluate strategy viability.
