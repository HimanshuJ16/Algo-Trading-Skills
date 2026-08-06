# Workflows for Strategy-Level vs Portfolio-Level Kill Switch

1. **Strategy Drawdown Audit**:
   - Monitor real-time mark-to-market equity per strategy.
2. **Strategy Isolation**:
   - Halt single strategy if strategy drawdown $\ge 10\%$.
3. **Master Portfolio Audit**:
   - Monitor total fund equity drawdown and count of tripped strategies.
4. **Master Circuit Breaker Dispatch**:
   - Liquidate all portfolio positions if portfolio drawdown $\ge 15\%$ or cascade threshold reached.