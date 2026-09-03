# Standards: Backtest Infrastructure Cost Budgeting

**Billing-model facts verified against the sources below on 2026-08-12.** Cloud
pricing and billing granularity change; re-verify before relying on a forecast.

## Modelling targets

| Item | Target | Rationale |
|---|---|---|
| Baseline profiling | Measure a real sample (~1% of the grid) | Every input is a per-unit measurement; a guessed baseline produces a confidently wrong total |
| Billing quantum | Set `minimum_billable_seconds` to the platform's minimum | The dominant error source for wide sweeps of short units, and it always understates |
| Parallel overhead | `parallel_overhead_factor` > 1.0 unless linearity is measured | Scheduler waste and contention are real; linear scaling is a claim, not a default |
| Spot interruption | Budget rework via `spot_interruption_overhead` | Interruptions are not exceptional; unfinished units are rerun and rebilled |
| Storage lifecycle | Ephemeral or object storage, short retention | Leaving large logs on block storage is expensive; retention is a direct multiplier |
| Egress | Count it when crossing cloud or region boundaries | Billed per GB and easy to forget for external datasets |

## Billing models — separable vs bundled

This is the assumption that most often makes a cost model wrong.

**Separable (what this skill models by default).** AWS Fargate pricing is
"calculated based on the vCPU, memory, Operating Systems, CPU Architecture, and
storage resources used", with CPU and memory as separate line items. Under this
model, per-vCPU-hour and per-GB-hour rates are independent and both apply.

Other serverless container platforms are commonly described as billing the same
shape. **Not verified here** — the Cloud Run pricing page could not be retrieved
in full at the time of writing, so this file makes no claim about its dimensions.
Check the provider's page before assuming separability.

**Bundled.** EC2 rents a whole instance at one rate: "Pricing is per
instance-hour consumed for each instance, from the time an instance is launched
until it is terminated or stopped", billed by the hour or second with a 60-second
minimum. The rate covers the instance's fixed vCPU and memory configuration
together, regardless of utilisation. Applying a separate per-GB-hour charge on
top double-counts memory.

To budget a bundled fleet: put the instance hourly rate in `cpu_hourly_rate`,
set `ram_hourly_rate=0.0`, and express `cpu_hours_per_unit` as instance-hours.

## Billing granularity

AWS Fargate bills "per second with a 1-minute minimum" for Linux tasks, and a
5-minute minimum for Windows containers.

The minimum applies **per task**, so it compounds across a sweep. Worked example
using the module's own arithmetic:

| Sweep | Raw compute | Billed with 60 s minimum |
|---|---|---|
| 5,000 units × 3.6 s | 5.0 vCPU-hours | 83.3 vCPU-hours (16.7×) |

Fargate also includes 20 GB of ephemeral storage per task by default, with
additional configured storage billed separately per GB-second — relevant if
per-unit output exceeds that allowance.

## Spot / preemptible capacity

AWS documents Spot Instances as "spare EC2 capacity for steep discounts in
exchange for returning them when Amazon EC2 needs the capacity back", and states
plainly: **"It is always possible that your Spot Instance might be
interrupted."** Interruption causes are capacity reclamation, the spot price
exceeding a specified maximum, and unmet request constraints. On interruption
the instance is terminated, stopped, or hibernated depending on the configured
behaviour.

AWS advertises savings of *up to* 90% versus on-demand. Two consequences for
budgeting:

1. "Up to 90%" is a ceiling, not an expectation. The realised discount varies by
   instance type, region, and availability zone. `spot_discount_multiplier`
   defaults to 0.3 (a 70% discount) as a **placeholder**, not a sourced figure —
   check the Spot Instance Advisor for your target instance and zone.
2. A flat discount multiplier understates true cost, because work lost to
   interruption is redone and rebilled. `spot_interruption_overhead` models that
   rework. It defaults to 0.0, which assumes perfect checkpointing and is
   optimistic for long-running units.

> An earlier revision of this file asserted spot "can reduce compute costs by
> 70-90%" without attribution and framed under-using spot as the pitfall. The
> figure is now attributed to AWS's "up to 90%" claim and qualified, and the
> pitfall is reframed: for a *budget guard*, over-crediting spot savings is the
> more dangerous error, because it biases the estimate downward.

## Storage-month convention

Storage is quoted per GB-month. This module converts retention days to months
using a fixed 30-day month (`DAYS_PER_BILLING_MONTH`). Providers prorate against
actual calendar months of 28–31 days, so a 365-day retention is modelled as
12.17 months and over-states by roughly 1.4%. The bias is deliberately
conservative for a budget guard; adjust the constant if you need exact
reconciliation rather than a pre-flight ceiling.

## Sources

| Claim | Source |
|---|---|
| Fargate bills on vCPU, memory, OS, CPU architecture, and storage as separate dimensions; per-second with 1-minute minimum (5-minute for Windows); 20 GB ephemeral storage included | AWS, *AWS Fargate Pricing* — https://aws.amazon.com/fargate/pricing/ |
| Spot Instances are spare capacity reclaimed when EC2 needs it; "it is always possible that your Spot Instance might be interrupted"; interruption causes and behaviours | AWS, *Spot Instance interruptions* — https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-interruptions.html |
| Spot savings of up to 90% versus on-demand; Spot Instance Advisor for per-instance rates | AWS, *Amazon EC2 Spot Instances Pricing* — https://aws.amazon.com/ec2/spot/pricing/ |
| EC2 bundled instance-hour billing: "Pricing is per instance-hour consumed for each instance, from the time an instance is launched until it is terminated or stopped"; by the hour or second with a 60-second minimum | AWS, *Amazon EC2 On-Demand Pricing* — https://aws.amazon.com/ec2/pricing/on-demand/ |
