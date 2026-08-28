# Workflows for Strategy Latency Budget Decomposition

All figures are **microseconds (µs)**. The sibling skill
`colocation-latency-budget-accounting` works in integer nanoseconds; a value moved
between the two without conversion is off by 1,000× and neither module detects it.

## 1. Derive the end-to-end budget before allocating it

The total budget is a property of the opportunity, not of the hardware. Establish it
first — from the decay of the signal, the duration of the race, or the staleness the
strategy can tolerate — then allocate it across stages. Allocating first and calling the
sum "the budget" produces a number that describes the current implementation rather than
the requirement it has to meet.

Record the derivation alongside the configuration. A stage budget with no traceable
parent is a number that will be defended in a review it cannot survive.

## 2. Capture stage durations out of band

This module consumes durations; it does not produce them.

- Timestamp each stage boundary into a fixed-size lock-free buffer on the hot path, and
  run every audit from a separate thread or process. Computing percentiles or writing log
  lines inside the execution thread adds I/O and allocation to the very path being timed.
- Keep all six boundary timestamps in **one clock domain**. NIC hardware timestamps live
  in the NIC's PTP hardware clock; `CLOCK_MONOTONIC` is a different clock. Subtracting
  across them yields an offset, not a duration — sometimes large enough to come out
  negative (which this module rejects), sometimes small enough to look plausible (which
  it cannot detect).
- Never use `time.time()` for a stage duration: `CLOCK_REALTIME` is stepped and slewed by
  NTP and can move backwards.
- **Count the instrumentation itself.** Each boundary read executes inside the path under
  audit. On a single-digit-microsecond budget, several timestamp reads are a real line
  item — either budget them explicitly or measure with hardware timestamps that sit
  outside the code path.
- A stage that reports `0.0` µs means the timer could not resolve it, not that the stage
  was free. The module accepts zero rather than rejecting it, because a genuinely
  sub-resolution stage is a real measurement — but a *column* of zeros is an
  instrumentation finding, not a fast stage.

## 3. Configure the allocation

```python
engine = StrategyLatencyBudgetDecompositionEngine(
    stage_sla_budgets={
        LatencyPipelineStage.INGRESS_NETWORK: 2.0,
        LatencyPipelineStage.MARKET_DATA_DECODE: 3.0,
        LatencyPipelineStage.SIGNAL_COMPUTATION: 10.0,
        LatencyPipelineStage.PRE_TRADE_RISK: 5.0,
        LatencyPipelineStage.EGRESS_ORDER_ENCODE: 5.0,
    },
    total_budget_us=25.0,
)
```

- Every stage must be listed. A partial map raises; it is never completed with a default.
- `total_budget_us` is independent of the sum. Omitting it defaults to the sum, i.e. an
  allocation with no headroom.
- If the allocation sums **above** the total, the engine warns and `unallocated_budget_us`
  goes negative. That configuration is legal and occasionally deliberate, but it means a
  trace can breach end-to-end with every stage inside its own share — and the remedy is
  re-allocation, not optimisation.
- If it sums **below** the total, the difference is headroom no stage owns. Decide
  deliberately whether that is a safety margin or an unassigned budget.

## 4. Ingest with quarantine

```python
reports, quarantined = [], 0
for trade_id, stage_latencies in captured_traces:
    try:
        reports.append(engine.decompose_tick_to_trade(trade_id, stage_latencies))
    except LatencyBudgetError:
        quarantined += 1
        logger.exception("quarantined trace %s", trade_id)
```

Catch per trace so one defective capture does not abort the batch, and **alert on the
quarantine rate**. Dropping bad traces silently biases the tail downwards: instrumentation
defects correlate with the slow path, so the traces you lose are disproportionately the
ones that would have breached.

Rejected classes, and what each produced before it was rejected:

| Input | Why it is rejected |
|---|---|
| Missing stage | Reads as zero latency; an incomplete trace passes an end-to-end budget it never met. |
| Unknown stage key | A typo contributes nothing and the mistyped stage silently reads as zero. |
| NaN | Compares `False` against every bound: nothing breaches, the total is NaN, and the report still renders. |
| Inf | Poisons the total and every percentile derived from it. |
| Negative | Proves a clock-domain error; the whole trace is bracketed by the same clocks and is wrong by an unknown amount. |
| Boolean | `True` is a valid `Real` worth 1.0 µs and is never a measurement. |
| Implausibly large (> 1e9 µs) | A unit error or an uninitialised timestamp difference. |

## 5. Read the single-trace report

| Field | Meaning |
|---|---|
| `is_within_budget` | Total against `total_budget_us`, strict `>` for a breach, compared unrounded. |
| `breached_stages` | Stages over their own allocated share. Independent of `is_within_budget`. |
| `primary_bottleneck_stage` | Stage furthest over its share; when none is over, the one with the least headroom in µs. Always defined. Ties break to the earliest stage. |
| `budget_deficit_us` | `max(0, total − total_budget_us)`. |
| `unallocated_budget_us` | `total_budget_us − sum(stage budgets)`. Negative means overcommitted. |
| `stage_share_of_total` | Each stage's fraction of the measured total. Where the time actually goes. |
| `stage_reduction_required_fraction` | Populated only on a breach — see below. |

`StageLatencyMeasurement` additionally exposes `excess_us`, `budget_utilization` and
`is_breached` per stage, for the ratio view rather than the microsecond view.

## 6. Target optimisation effort with the Amdahl bound

On a breach, `stage_reduction_required_fraction[stage]` is `deficit / stage_latency`: the
fraction of that stage you would have to remove to bring the trace back inside budget.

- **< 1.0** — the stage can close the deficit on its own, by removing that fraction of it.
- **≥ 1.0** — it cannot, even if eliminated entirely. The most any single stage can remove
  from the total is its own duration.

Read this together with `stage_share_of_total` before assigning engineering work. A stage
that is 4% of the total cannot recover a 20% overshoot, however slow it feels; the deficit
has to come from a stage large enough to contain it, or from several stages together, or
from re-allocating the budget.

## 7. Profile the tail over a batch

```python
profile = engine.profile_batch(reports)
```

- Requires reports produced under the *same* allocation; a mixed batch is rejected,
  because excess figures computed against different budgets are not comparable.
- Percentiles are nearest rank, so every figure is an observed value.
- **`p99_total_us` is the end-to-end tail. `sum_of_stage_p99_us` is not.** The second is
  what stage-by-stage budgeting would predict; only the first is a measurement.
  `comonotonic_gap_us` is `sum_of_stage_p99_us − p99_total_us`:

| Gap | Reading | What to do |
|---|---|---|
| ≈ 0 | Stages spike together | Find the one shared cause — GC, scheduler preemption, IRQ storm — not five separate ones. |
| > 0 | Independent stalls, each resolved by its own stage P99 | Stage-by-stage budgeting over-provisions; the total has more headroom than the sum suggests. |
| < 0 | Stalls spread too thinly for any stage P99 to resolve | Stage-by-stage budgeting **under**-provisions and would approve a pipeline already over its end-to-end P99. Budget from the total. |

- `status` is `LATENCY_BUDGET_BREACH` at any sample count once a trace has exceeded the
  total — an over-budget trace was genuinely observed. An approval requires at least 100
  traces; below that, `LATENCY_BUDGET_INSUFFICIENT_SAMPLES`, because the nearest rank for
  P99 lands on the batch maximum and the reported "P99" is simply the worst trace seen.
  *No breach observed* is not *within budget*.
- `breach_rate` is the fraction of traces over the total budget. A clean P99 with a
  non-zero breach rate below 1% means the breaches sit above the audited percentile —
  audit a rarer one with `latency-monitoring-percentile-based-slas`.

## 8. Re-derive the allocation when anything upstream changes

A budget allocation is fitted to a strategy, a venue, an instrument and a code path. When
any of those change, the allocation is a guess until it is re-derived — and the stage
percentiles it was built from need re-measuring, not carrying forward.
