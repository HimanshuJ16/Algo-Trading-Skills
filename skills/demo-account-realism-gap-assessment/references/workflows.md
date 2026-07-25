# Deep Workflow Reference — demo-account-realism-gap-assessment

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Log Execution Metric Capture**:
   - Record order arrival price, fill price, submission timestamp, fill timestamp, and fill quantity for both demo and live executions.

2. **Compute Latency & Slippage Discrepancies**:
   - Compute mean latency (ms) and mean slippage (basis points) across demo vs live datasets.

3. **Calculate Realism Score ($R \in [0, 1]$)**:
   - Combine latency ratio (30%), exponential decay slippage penalty (40%), and fill rate ratio (30%).

4. **Apply Sharpe Ratio Haircut**:
   - Multiply backtest/demo Sharpe ratio by $R$ before capital allocation sign-off.

## Production Implementation Reference

- Reference code: `scripts/realism_assessor.py` (`DemoRealismAssessor`, `ExecutionLog`, `RealismAssessmentResult`).
- Automated unit tests: `scripts/test_realism_assessor.py`.
