# Workflows for Portfolio-Level Stop-Loss Independent of Strategy Stops

1. **Portfolio NAV Calculation**:
   - Aggregate current cash and market value of all open positions across sub-strategies.
2. **Drawdown Monitoring**:
   - Calculate Daily Drawdown % and Peak-to-Trough Drawdown %.
3. **Emergency Circuit Breaker Enforcement**:
   - If drawdown limit is breached, trigger immediate global position flattening and lock trading session.
4. **Audit Report Generation**:
   - Output structured portfolio stop report.