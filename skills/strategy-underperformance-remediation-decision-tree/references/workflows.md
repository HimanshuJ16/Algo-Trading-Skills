# Workflows for Strategy Underperformance Remediation Decision Tree

1. **Metrics Harvesting**:
   - Collect live Sharpe, backtest Sharpe, peer Sharpe, slippage, and hypothesis validity.
2. **Sequential Triage Audit**:
   - Run through Node 1 (Hypothesis), Node 2 (Execution), Node 3 (Regime Shift), and Node 4 (Parameter Drift).
3. **Remediation Classification**:
   - Assign action: `DECOMMISSION`, `OPTIMIZE_EXECUTION`, `DEGRADE_CAPITAL`, `RECALIBRATE`, or `MAINTAIN`.
4. **Governance Handoff**:
   - Route remediation instructions to Risk Committee and Strategy Operations team.