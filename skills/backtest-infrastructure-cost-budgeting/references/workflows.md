# Workflow: Backtest Infrastructure Cost Budgeting

1. **Benchmark a Sample Run**
   Run the backtest on a small subset (e.g., 1 instrument, 1 parameter set). Note the execution time, peak RAM, and disk space used.

2. **Define Cloud Pricing**
   Check your cloud provider's pricing for the instance types you plan to use. Instantiate `CloudPricing`.

3. **Estimate Costs**
   Create a `BacktestJobSpec` with the full dimensions of your planned run. Pass it to `BacktestCostBudgeter`.

4. **Review and Optimize**
   If `is_over_budget` is True, consider:
   - Reducing the parameter space.
   - Using spot/preemptible instances.
   - Optimizing the code (e.g., vectorized operations) to lower `cpu_hours_per_unit`.
