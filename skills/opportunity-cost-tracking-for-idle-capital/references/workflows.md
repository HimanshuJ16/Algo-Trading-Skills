# Workflows for Opportunity Cost Tracking for Idle Capital

1. **Idle Capital Ratio & Drag Calculation**:
   - Calculate idle capital ratio and gross USD return drag against SOFR benchmark.
2. **Net Yield Calculation**:
   - Deduct transaction sweep cost from gross drag to determine net yield gain.
3. **Cash Sweep Recommendation**:
   - Recommend automated cash sweep if idle cash >= min threshold and net yield gain > 0.
4. **Audit Report Generation**:
   - Output structured opportunity cost report.