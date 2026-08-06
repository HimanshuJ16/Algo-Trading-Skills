# Workflows for Strategy Lifecycle Retirement Criteria

1. **Performance Metrics Harvesting**:
   - Collect live vs backtest Sharpe, Drawdown, IR, and IC t-stats.
2. **Quantitative Guardrail Audit**:
   - Audit IR ($\ge 0.50$), Drawdown ($\le 1.5\times$), IC t-stat ($\ge 1.96$), and Return Drift ($\ge -40\%$).
3. **Decision Tree Classification**:
   - Classify into `ACTIVE_HEALTHY`, `NEEDS_REVIEW`, `REDUCE_ALLOCATION`, or `MANDATORY_RETIREMENT`.
4. **Decommissioning Handoff**:
   - Hand off retired strategies to the decommissioning & position unwind pipeline.