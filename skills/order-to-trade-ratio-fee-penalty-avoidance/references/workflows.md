# Workflows for Order-to-Trade Ratio Fee Penalty Avoidance

1. **Session OTR Metrics Calculation**:
   - Compute total messages (orders + cancels + modifies) and calculate Count and Volume OTR.
2. **Excess Messages & Penalty Surcharge Computation**:
   - Calculate excess messages beyond allowable threshold and compute total accrued penalty fee.
3. **Defensive Order Throttling Guard**:
   - Evaluate warning (80%) and breach (100%) thresholds to trigger order throttling or freezes.
4. **Audit Report Generation**:
   - Output structured OTR report.