# Workflows for Risk Metric Recalculation Frequency Tuning

1. **Metric Tier Classification**:
   - Assign risk metrics to Tier 1 (Tick), Tier 2 (2s), Tier 3 (30s), Tier 4 (300s).
2. **Real-Time P&L Velocity Evaluation**:
   - Calculate absolute P&L change per second ($\frac{|\Delta \text{PnL}|}{\Delta t}$).
3. **Dynamic Cadence Acceleration**:
   - Switch scheduler to accelerated intervals if P&L velocity > threshold.
4. **Execution & CPU Audit**:
   - Calculate due metrics and log theoretical CPU cycles saved.
