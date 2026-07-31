# Workflows for Queue Position Modeling for Passive Orders

1. **Queue Initialization**:
   - Track initial volume ahead ($Q_{\text{ahead}}$) at order placement time.
2. **Dynamic Queue Priority Update**:
   - Update $Q_{\text{ahead}}$ upon receiving fills and cancellations at the price level.
3. **Fill Probability & Defensive Audit**:
   - Calculate fill probability and evaluate defensive cancellation triggers.
4. **Audit Report Generation**:
   - Output structured queue position report.