# Workflows for Strategy Capacity Estimation Before Scaling Capital

1. **Parameter Definition**:
   - Inputs: Gross Return, Volatility, Daily Turnover %, ADV USD, Half-Spread bps.
2. **Market Impact Modeling**:
   - Apply Almgren-Chriss square-root impact formula.
3. **Decay Curve Generation**:
   - Calculate Net Sharpe ratio across AUM steps.
4. **Capacity Limit Verification**:
   - Determine maximum AUM before Net Sharpe drops below threshold.