---
name: backtest-infrastructure-cost-budgeting
description: Budgeting compute/storage cost for large-scale backtesting before it
  becomes a surprise cloud bill.
domain: Backtesting
subdomain: Infrastructure
tags:
- backtesting
- cost
- budgeting
- cloud
brokers_frameworks:
- AWS
- GCP
version: "1.0.0"
author: System
license: MIT
---

# Backtest Infrastructure Cost Budgeting

## When to Use
Use this skill before kicking off massive grid searches or tick-level backtests over thousands of instruments. Large-scale backtesting can quietly consume thousands of dollars in cloud computing resources if not carefully budgeted in advance.

## Prerequisites
- Basic knowledge of cloud instance pricing (e.g., AWS EC2, GCP Compute).
- Rough estimates of single-backtest resource consumption (memory, time, storage).
- Total number of parameter combinations and instruments.

## Workflow
1. Run a small representative sample of the backtest.
2. Measure the CPU time, peak memory usage, and storage generated.
3. Feed these metrics into the `BacktestCostBudgeter` alongside the total sweep space.
4. Review the estimated cloud cost before scaling up.

## Common Pitfalls
- **Ignoring Storage Costs:** Huge tick databases or massive output logs can dominate costs over CPU hours.
- **Assuming Linear Scaling:** Sometimes parallel overhead or database locks cause costs to scale non-linearly.
- **Forgetting Spot Instances:** Not budgeting for the cost savings of spot/preemptible instances.

## Verification
- Cross-check the budgeter's estimate with actual bills after running a 10% scale job.

## Related Skills
- `backtest-parameter-sensitivity-analysis`
