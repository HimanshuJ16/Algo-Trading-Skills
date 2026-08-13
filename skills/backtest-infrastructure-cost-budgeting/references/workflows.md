# Workflow: Backtest Infrastructure Cost Budgeting

Billing-model facts and sources are in `references/standards.md`.

## 1. Benchmark a representative sample

Run one instrument × one parameter set and measure:

| Measure | Feeds |
|---|---|
| Wall/CPU time per unit | `cpu_hours_per_unit` |
| Peak RSS while the unit runs | `memory_gb_required` |
| Output written per unit | `storage_gb_per_unit` |
| Bytes pulled across a cloud/region boundary | `egress_gb_per_unit` |

Measure the *slowest realistic* unit, not the fastest. A grid's cost is driven
by its expensive corners — a wide parameter set on a liquid, high-tick-count
instrument — and averaging those away produces a forecast that is optimistic
exactly where it matters.

## 2. Define pricing for your billing model

**Separable (Fargate-style):**

```python
pricing = CloudPricing(
    cpu_hourly_rate=...,                 # per vCPU-hour, your region
    ram_hourly_rate=...,                 # per GB-hour
    storage_monthly_rate_per_gb=...,     # per GB-month
    minimum_billable_seconds=60.0,       # Fargate: per-second, 1-minute minimum
    spot_interruption_overhead=0.15,     # budget rework from interruptions
    egress_rate_per_gb=...,
)
```

**Bundled (EC2-style):**

```python
pricing = CloudPricing(
    cpu_hourly_rate=0.192,   # the whole instance's hourly rate
    ram_hourly_rate=0.0,     # memory is already inside that rate
    storage_monthly_rate_per_gb=...,
    minimum_billable_seconds=60.0,       # EC2: per-second, 60-second minimum
)
```

and express `cpu_hours_per_unit` as instance-hours per unit.

Pull current rates from the provider's pricing page for your region — this
module hard-codes none, deliberately.

## 3. Estimate

```python
job = BacktestJobSpec(
    instruments=1200,
    parameter_combinations=480,
    cpu_hours_per_unit=0.08,
    memory_gb_required=6.0,
    storage_gb_per_unit=0.015,
    storage_retention_days=14.0,
    parallel_overhead_factor=1.2,   # 20% coordination/contention allowance
)

budgeter = BacktestCostBudgeter(pricing, max_budget=2_500.0)
estimate = budgeter.estimate_costs(job, use_spot_instances=True)
```

Invalid inputs raise `CostBudgetError` rather than producing a number. That is
deliberate: a NaN propagating to `total_cost` compares False against any budget,
so the guard would silently pass the job it exists to stop.

## 4. Read the result

| Key | Meaning |
|---|---|
| `total_units` | instruments × parameter_combinations |
| `billable_cpu_hours` | After the per-unit billing floor and overhead factor |
| `cpu_cost` / `ram_cost` / `storage_cost` / `egress_cost` | Components |
| `total_cost` | Sum |
| `budget_headroom` | `max_budget - total_cost`; negative when over |
| `is_over_budget` | True only when cost **strictly exceeds** budget |

A total exactly equal to the budget is treated as within it.

## 5. Act on an over-budget result

In rough order of leverage:

1. **Cut the grid.** Cost is linear in `total_units`; halving the parameter space
   halves compute. See `backtest-parameter-sensitivity-analysis` for deciding
   which parameters actually matter.
2. **Shorten retention.** `storage_retention_days` is a direct multiplier on
   storage cost and usually the cheapest concession to make.
3. **Reduce `cpu_hours_per_unit`.** Vectorise, cache, or narrow the date range.
4. **Consider spot** — but budget `spot_interruption_overhead` alongside the
   discount, and only if units are checkpointed or short enough to redo cheaply.
5. **Right-size memory.** Under separable billing, `memory_gb_required` is a
   direct multiplier on RAM cost for the whole sweep.

## 6. Reconcile after a pilot

Run ~10% of the sweep, compare against the actual bill, then **correct the
per-unit inputs, not the total**. A fudge factor applied to the output hides
which assumption was wrong and will not survive the next change to the grid.

Typical reasons a forecast comes in low:

- The per-task billing minimum was not modelled (largest single cause for wide
  sweeps of short units).
- Spot interruptions caused rework that a flat discount multiplier did not cover.
- Scaling was assumed linear when contention made it super-linear.
- Retries and failed units were billed but not counted.
- Egress across a cloud or region boundary was omitted.

## Production Implementation Reference

- Reference code: `scripts/cost_budgeter.py` (`BacktestCostBudgeter`,
  `BacktestJobSpec`, `CloudPricing`, `CostBudgetError`).
- Automated unit tests: `scripts/test_cost_budgeter.py`.
- Billing-model facts and sources: `references/standards.md`.
