# Workflow — backtest-parameter-sensitivity-analysis
## Procedure
1. Define parameter grid with ranges and step sizes.
2. Execute backtest for each grid point.
3. Compute Sharpe gradient between adjacent points.
4. Classify as ROBUST (flat plateau) or FRAGILE (sharp peak).
## Reference
- `scripts/sensitivity_analyzer.py`, `scripts/test_sensitivity_analyzer.py`
