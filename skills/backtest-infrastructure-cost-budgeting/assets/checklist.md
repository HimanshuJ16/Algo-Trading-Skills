# Pre-Flight Checklist: Backtest Infrastructure Cost Budgeting

## Baseline measurement
- [ ] A real sample run executed — no input is a guess
- [ ] The sample is a **slow, realistic** unit, not the fastest one (grids are priced by their expensive corners)
- [ ] CPU time, peak RSS, output storage, and egress all measured per unit
- [ ] Sweep dimensions confirmed (instruments × parameter combinations), including any retry allowance

## Billing model
- [ ] Determined whether the target platform bills **separably** (per vCPU-hour + per GB-hour, e.g. Fargate) or **bundled** (per instance-hour, e.g. EC2)
- [ ] For bundled billing: instance rate in `cpu_hourly_rate`, `ram_hourly_rate=0.0`, and `cpu_hours_per_unit` expressed as instance-hours — memory not double-counted
- [ ] Rates pulled from the provider's current pricing page for the **target region**, not from memory or this repo
- [ ] `minimum_billable_seconds` set to the platform's per-task minimum (Fargate Linux: 60 s; EC2: 60 s) — or deliberately 0.0
- [ ] Checked whether per-unit runtime falls below that minimum; if so, the naive estimate understates badly

## Overheads that bias estimates downward
- [ ] `parallel_overhead_factor` set above 1.0 unless linear scaling has actually been measured
- [ ] If using spot: `spot_interruption_overhead` budgeted for rework — interruption is not exceptional
- [ ] `spot_discount_multiplier` checked against the Spot Instance Advisor for the target instance and zone, not left at the 0.3 placeholder
- [ ] Failed and retried units counted in the sweep dimensions
- [ ] Egress accounted for if datasets cross cloud or region boundaries
- [ ] Storage retention set to the real value, not the 30-day default

## Guard integrity
- [ ] Every input is finite and non-negative — `CostBudgetError` is raised otherwise, and that is the desired behaviour
- [ ] No code path swallows `CostBudgetError` and proceeds with a default
- [ ] `is_over_budget` wired into the launcher so an over-budget sweep is actually blocked, not merely reported
- [ ] Understood that a total exactly equal to the budget counts as **within** budget
- [ ] Budget limit agreed with whoever owns the cloud account before the run

## Validation
- [ ] Run `python -m unittest discover -s skills/backtest-infrastructure-cost-budgeting/scripts` — 30 tests, all pass
- [ ] Deliberately oversized sweep confirmed to flag `is_over_budget`
- [ ] 10% pilot run executed and reconciled against the actual bill
- [ ] Divergence corrected in the **per-unit inputs**, not by applying a fudge factor to the total

## Sign-off
- Reviewed by: ___________________________
- Date: ___________________________
- Cloud account owner notified of budget ceiling: ___________________________
- Rates verified for region / date: ___________________________
