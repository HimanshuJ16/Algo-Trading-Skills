# Workflows for Risk Budget Allocation Across Time Horizons

1. **Horizon Bucket Definition**:
   - Specify holding period, risk allocation %, volatility target, and max drawdown per horizon.
2. **Position Size Scalar Computation**:
   - Ratio of horizon vol target to portfolio vol target.
3. **Total Allocation Validation**:
   - Verify sum of allocated risk % <= 100%.
4. **Report Generation**:
   - Output structured risk budget report with per-horizon allocations and over-allocation flags.