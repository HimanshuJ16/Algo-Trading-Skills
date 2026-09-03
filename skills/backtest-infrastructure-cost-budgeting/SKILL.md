---
name: backtest-infrastructure-cost-budgeting
description: >-
  Use before launching a large grid search or tick-level sweep, to forecast cloud
  compute and storage spend with spot interruption overhead, per-task billing minimums
  and non-linear scaling, rather than discovering it on the invoice.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting, cost, budgeting, cloud, capacity-planning, spot-instances
  brokers_frameworks: "AWS Fargate / EC2; Google Cloud Run / Compute Engine"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill before kicking off massive grid searches or tick-level backtests over thousands of instruments. Large-scale backtesting can quietly consume thousands of dollars of cloud spend if it is not budgeted in advance, and the bill arrives long after the decision to run was made.

## When NOT to Use

- **As a billing reconciliation tool.** This estimates ahead of a run. For tracking actual spend against forecast, see `cost-monitoring-for-cloud-trading-infrastructure`.
- **As a live cost cut-off.** It produces a number; it does not stop a running job. Wire `is_over_budget` into your launcher yourself.
- **For a single small backtest.** Below a few dollars the estimation effort exceeds the spend.
- **Without a measured baseline.** Every input is a per-unit measurement. Guessed inputs produce a confidently wrong total — profile a real sample first.

## Which Billing Model This Fits

Costs are modelled as **separately metered vCPU-hours and GB-hours**. That is how serverless container platforms bill: AWS Fargate charges "based on the vCPU, memory, Operating Systems, CPU Architecture, and storage resources used", with CPU and memory as separate line items.

**EC2 does not bill this way.** AWS states "pricing is per instance-hour consumed for each instance, from the time an instance is launched until it is terminated or stopped" — one rate covering the instance's fixed vCPU and memory together, whether or not you use it. To budget an instance fleet with this tool:

```python
pricing = CloudPricing(
    cpu_hourly_rate=0.192,   # full instance hourly rate
    ram_hourly_rate=0.0,     # memory is already inside the instance price
    storage_monthly_rate_per_gb=0.10,
)
```

and express `cpu_hours_per_unit` as *instance-hours* per unit. Leaving `ram_hourly_rate` at a non-zero value under bundled billing double-counts memory you have already paid for.

No prices are hard-coded in this skill. Published rates change and vary by region, CPU architecture, OS, and commitment level — pull current numbers from the provider's pricing page for your region.

## Prerequisites

- A measured baseline from a small representative run: CPU time, peak memory, storage produced, and egress per unit.
- Total sweep dimensions (instruments × parameter combinations).
- Current rates for your region and billing model.

## Workflow

1. **Run a representative sample** — one instrument, one parameter set — and measure CPU time, peak RAM, storage produced, and any egress.
2. **Choose the billing model** and populate `CloudPricing` accordingly (separable vCPU/GB, or bundled instance rate with `ram_hourly_rate=0.0`).
3. **Set the billing quantum.** Fargate bills "per second with a 1-minute minimum". A sweep of thousands of few-second units pays that minimum thousands of times: 5,000 units of 3.6 s each bill as 83 vCPU-hours, not 5 — a 16.7× difference. Set `minimum_billable_seconds=60.0` on Fargate, or `0.0` where billing is genuinely continuous.
4. **Budget the overheads you know you have.** `parallel_overhead_factor` above 1.0 covers scheduler waste, contention, and coordination. Perfect linear scaling is the assumption to justify, not the default to assume.
5. **If using spot, budget the interruptions too.** AWS states "it is always possible that your Spot Instance might be interrupted", and interrupted work has to be redone. `spot_interruption_overhead=0.15` budgets 15% rework on top of the discounted rate. Leaving it at 0.0 assumes perfect checkpointing.
6. **Review `is_over_budget` before scaling up**, and act on it: cut the parameter space, shorten retention, or optimise `cpu_hours_per_unit`.

> Full procedure: see `references/workflows.md`.
> Rate sources and modelling assumptions: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the spot discount as free money.** A flat multiplier with no interruption allowance biases the budget *down* — the dangerous direction for a guard. Spot instances are reclaimed when the provider needs capacity back, and partially complete units must be rerun.
- **Ignoring the per-task billing minimum.** The single largest error source for wide sweeps of short units, and it always understates.
- **Assuming linear scaling.** Parallel overhead, database locks, and scheduler queuing mean 10,000 units rarely cost exactly 100× what 100 units cost. `parallel_overhead_factor` exists to make that assumption explicit.
- **Ignoring storage and egress.** Huge tick databases and verbose output logs can dominate CPU cost, and pulling datasets across cloud boundaries is billed per GB.
- **Double-counting RAM under bundled instance billing.** See the billing-model section above.
- **Feeding in guessed per-unit numbers.** The estimate inherits the accuracy of its inputs and reports the result with the same confidence either way.
- **Trusting a NaN.** `float('nan') > budget` is False, so before v2.0 a single NaN input silently reported the sweep as within budget. Non-finite and negative inputs are now rejected outright.
- **Budgeting only the successful path.** Failed and retried units are billed too.

## Verification

- Run `python -m unittest discover -s skills/backtest-infrastructure-cost-budgeting/scripts` — 30 tests, 100% pass rate.
- Cross-check the estimate against the actual bill after running a 10% scale job, then correct the per-unit inputs rather than the total.
- Confirm `is_over_budget` is True for a deliberately oversized sweep, and that a NaN or negative input raises `CostBudgetError` rather than passing the guard.
- Confirm the exact-budget boundary behaves as intended: a total equal to the budget is treated as **within** budget.

## Related Skills

- `backtest-parameter-sensitivity-analysis` — shrinking the sweep space is the cheapest cost lever
- `cost-monitoring-for-cloud-trading-infrastructure` — tracking actual spend against this forecast
- `capacity-planning-for-symbol-universe-growth` — how the sweep dimensions grow over time
- `walk-forward-hyperparameter-search-budget` — bounding the search before it is priced
- `load-testing-before-scaling-to-new-instrument-universe` — validating the per-unit baseline at scale
