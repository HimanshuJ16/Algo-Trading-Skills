# Workflows for Capital Reallocation

1. **Daily Settlement**: At EOD (End of Day), aggregate the realized and unrealized PnL for each active strategy.
2. **Performance Scoring**:
   - Compute the rolling 30-day and 90-day Sharpe ratio for each strategy.
   - Compute the Half-Kelly fraction based on the trailing 100 trades.
3. **Target Weight Calculation**:
   - The engine normalizes the performance scores into target portfolio weights ($0.0$ to $1.0$).
   - Apply floor and ceiling constraints (e.g., no strategy drops below 5%, no strategy exceeds 40%).
4. **Delta Generation**:
   - Compare `current_capital` against `target_capital`.
   - Issue adjustment instructions to the OMS risk layer.
5. **Slow Unwinding**: If a strategy is being scaled down, do not force market orders. Simply lower its risk limit and let natural trade exits free up the capital.