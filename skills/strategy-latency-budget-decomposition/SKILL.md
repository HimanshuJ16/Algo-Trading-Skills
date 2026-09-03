---
name: strategy-latency-budget-decomposition
description: >-
  Use when a strategy has an end-to-end tick-to-trade budget and it must be split across
  pipeline stages, checking the allocation is feasible and showing where optimisation
  effort actually pays.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: latency-budget, budget-allocation, tick-to-trade, pipeline-bottleneck, p99-latency, nearest-rank, amdahl, sla-breach
  brokers_frameworks: "HdrHistogram (nearest-rank semantics); MiFID II RTS 25; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a strategy has an end-to-end tick-to-trade budget and you need to decide **how to split it across pipeline stages, whether the split is feasible, and where optimisation effort will actually pay.** It is the skill the other latency skills point at when they need a stage budget derived rather than assumed.

The budget itself comes from the opportunity, not from the hardware. Aquilina, Budish and O'Neill's message-level study of FTSE 100 order books found the modal latency-arbitrage race is decided in **5–10 microseconds**. A strategy competing in those races has a budget in single-digit microseconds; a strategy capturing a slower signal may have milliseconds. Nothing in this module tells you which you are — it audits the split of a total you supply.

Three things this module exists to get right:

1. **The total budget is not the sum of the stage budgets.** They are independent numbers, and both directions of mismatch matter. An allocation summing *above* the total means a trace can breach end-to-end with every stage inside its own share — the code is fine and the budget is wrong. An allocation summing *below* the total leaves headroom no stage owns.
2. **"Bottleneck" needs one definition, not two.** This module always names the stage furthest over its allocated share, and when nothing is over, the stage with the least remaining headroom in microseconds. Ranking by raw duration instead just names whichever stage was given the largest budget.
3. **Per-stage P99s do not add up to the total P99, and the error is not conservative.** See `profile_batch` below and the Pitfalls.

## When NOT to Use

- **As the instrumentation.** This module reads no clock and times nothing. It audits stage durations captured elsewhere, off the hot path. Capturing them belongs to `tick-to-trade-latency-measurement` and `network-interface-level-tick-timestamping`.
- **For in-host nanosecond trace accounting.** `colocation-latency-budget-accounting` is the NIC-ingress-to-NIC-egress skill: integer nanoseconds, its own phase model, trace quarantine on non-monotonic timestamps. This skill works in microseconds at strategy-stage granularity and is about the *allocation*, not the trace. Use that one for hot-path forensics, this one for budget design.
- **For general percentile/SLA machinery.** If the question is "does this one latency series meet its SLA", `latency-monitoring-percentile-based-slas` adds coordinated-omission correction, P99.9 and fleet pooling. This skill computes only the P99 it needs to audit an allocation.
- **With the shipped budgets unchanged.** `2 / 3 / 10 / 5 / 5` µs is an illustrative split of 25 µs so the module runs out of the box. **No regulator, exchange, vendor or standards body publishes per-stage tick-to-trade budgets** — see `references/standards.md`. Derive yours from the opportunity your strategy is actually trading.
- **As a live circuit breaker.** This is an out-of-band audit over captured traces. Halting on a latency excursion belongs in `risk-control-latency-budget` and `kill-switch-and-drawdown-circuit-breakers`.
- **As a regulatory timestamping record.** MiFID II RTS 25 governs *business clock* accuracy against UTC for reportable events; it is not a latency SLA, and the relative durations this module consumes are not a compliant timestamping record.

## Prerequisites

- A stage duration in **microseconds for every one of the five stages** of each trace: `INGRESS_NETWORK`, `MARKET_DATA_DECODE`, `SIGNAL_COMPUTATION`, `PRE_TRADE_RISK`, `EGRESS_ORDER_ENCODE`. An incomplete trace is rejected, not zero-filled.
- **Durations from one clock domain.** NIC hardware timestamps and `CLOCK_MONOTONIC` are different clocks; subtracting across them yields an offset, not a duration. Convert before building a trace — `hardware-timestamping-vs-software-timestamping-accuracy` and `clock-synchronization-ptp-for-trading-hosts` cover the conversion.
- **A timer that can resolve the stages you are budgeting.** Under RTS 25 an HFT firm's *business* clock need only be accurate to 100 µs with 1 µs granularity. That clock cannot measure a 2 µs stage at all; per-stage timing needs an in-host monotonic counter or NIC hardware timestamps, and a stage reported as `0.0` µs means the timer could not resolve it, not that the stage was free.
- An end-to-end `total_budget_us` derived from the opportunity, and a per-stage allocation of it.
- At least 100 traces before a batch P99 means anything.

## Units

Every latency, budget and percentile in this module is **microseconds (µs)** as a Python `float`. `colocation-latency-budget-accounting`, the nearest sibling, is in integer **nanoseconds** — a value moved between the two without conversion is off by 1,000× and neither module will catch it. Reported fields are rounded to 3 dp (1 ns); **every budget comparison runs on the unrounded value**, so a 25.000001 µs total against a 25 µs budget is a breach and not a 25.0 µs pass.

## Workflow

1. **Set the total budget first, then allocate it.** Construct `StrategyLatencyBudgetDecompositionEngine(stage_sla_budgets, total_budget_us)`. Every stage must appear in `stage_sla_budgets` — a partial map raises rather than inventing a budget for the stages you left out. Omitting `total_budget_us` defaults it to the sum of the stage budgets, i.e. an allocation with no headroom. If the allocation sums above the total the engine logs a warning and sets `unallocated_budget_us` negative: that configuration is legal, but it means a breach can occur with no stage at fault, and the fix is re-allocation rather than optimisation.
2. **Reject unusable traces rather than repairing them.** `decompose_tick_to_trade` raises on a missing stage, an unknown stage key, and on NaN, Inf, negative, boolean or non-numeric durations. Each of these otherwise yields a *passing* report: a missing stage reads as zero latency, a NaN breaches nothing while making the total NaN, and a negative stage subtracts from the total and can offset a real breach into an apparent pass. Catch the error per trace, count the quarantine, and continue — a quarantine rate that is silently dropped biases the tail downwards, because instrumentation defects correlate with the slow path.
3. **Read the single-trace verdict against the right denominator.** `is_within_budget` compares the total to `total_budget_us` (strict `>` for a breach, so landing exactly on budget is inside). `breached_stages` lists stages over their own share. These are independent: either can fire without the other.
4. **Take `primary_bottleneck_stage` as "furthest over its share".** Always defined, breach or not; negative `excess_us` on the named stage means nothing was over and this was merely the tightest. Ties break towards the earliest stage in hot-path order. If you want the ratio view instead of the microsecond view, each `StageLatencyMeasurement` exposes `budget_utilization`.
5. **Check what a stage can arithmetically achieve before optimising it.** On a breach, `stage_reduction_required_fraction[stage]` is `deficit / stage_latency` — the fraction of that stage you would have to remove to get back inside budget. **A value above 1.0 means that stage cannot close the gap even if deleted entirely** (Amdahl's bound: the most one stage can remove from the total is its own duration). Engineers routinely attack the stage that feels slow rather than the one capable of closing the deficit; this is the number that settles it.
6. **Profile the tail over a batch, never from one trace.** `profile_batch(reports)` returns nearest-rank P50/P95/P99 of the per-trace totals plus per-stage P99s. **Read `p99_total_us`. Do not sum `stage_p99_us`.** `comonotonic_gap_us` is the signed difference between the sum and the measurement, and it is a diagnostic rather than a bound:
   - **≈ 0** — the stages spike together. One shared cause (GC, scheduler preemption, IRQ storm), not five.
   - **> 0** — the stages stall independently and each stall is common enough for its own stage P99 to see it; summing double-counts excursions that rarely coincide.
   - **< 0** — the dangerous case. Stalls are spread thinly enough across stages that no single stage's P99 resolves them, while the totals do. Stage-by-stage budgeting would approve a pipeline already over its end-to-end P99.
7. **Do not read an approval off a batch too small to resolve P99.** A breach is reported at any sample count — an over-budget trace was genuinely observed. An approval requires at least 100 traces; below that the status is `LATENCY_BUDGET_INSUFFICIENT_SAMPLES`, because the nearest rank for P99 lands on the batch maximum and the "P99" is just the worst trace seen.

> Full procedure: see `references/workflows.md`.
> Standards, sources, and what nobody publishes: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Adding up per-stage P99s to get an end-to-end P99.** This is exact only when the stages are comonotonic — when they spike together. When they stall *independently* it is an **under**-estimate, because the total sees the union of the stages' stall events while each stage's P99 sees only its own. Five stages each stalling on a different 1% of 100 traces put 5% of totals into the tail: the measured total P99 is 19.0 µs while the stage P99s sum to 11.0 µs (worked in the tests). This is the quantile functional failing to be subadditive over independent skewed positions, the same structure as the standard defaultable-bond counterexample for Value-at-Risk. **Measure the total.**
- **Defining the total budget as the sum of the stage budgets.** It makes over-commitment unrepresentable, so the one configuration that produces breaches with no stage at fault is the one you cannot see.
- **Ranking stages by raw duration and calling the biggest the bottleneck.** It names whichever stage was allocated the most time. A signal stage at 7 µs of a 10 µs share has 3 µs spare while an ingress stage at 1.5 µs of a 2 µs share has 0.5 µs — the second is the one about to spend someone else's budget.
- **Optimising a stage that cannot close the deficit.** A stage that is 4% of the total cannot recover a 20% overshoot even if it is deleted. Check `stage_reduction_required_fraction` before assigning the work.
- **Treating a missing stage measurement as a fast stage.** A dropped instrumentation point defaults to zero in any implementation that uses `dict.get(stage, 0.0)`, and an incomplete trace then passes an end-to-end budget it never satisfied.
- **Letting a NaN into a stage duration.** NaN compares `False` against every bound: no stage breaches, and the report is emitted with a NaN total that renders on a dashboard as a blank rather than an alarm.
- **Filtering out negative stage durations and auditing the rest.** A negative duration proves the two timestamps came from different clock domains or ran backwards. Every other stage in that trace was bracketed by the same clocks and is wrong by an unknown amount. The trace is unusable, not partially usable.
- **Timing the stages with the clock that satisfies RTS 25.** A business clock permitted 100 µs of divergence from UTC and 1 µs of granularity cannot resolve a per-stage microsecond budget. RTS 25 compliance and stage-level measurement are separate problems solved by separate clocks.
- **Forgetting that instrumentation is inside the budget.** Every stage boundary adds a timestamp read to the path being measured, and the reads land in the hot path whose latency is under audit. Budget them, or measure with hardware timestamps that do not.
- **Reading a mean and calling the path budgeted.** A pipeline that is 12 µs on 999 ticks and 4 ms on the thousandth averages out to something reassuring while losing the one trade that mattered.
- **Reusing millisecond intuitions for a race-speed strategy.** A margin of 5–10 µs decides a latency-arbitrage race. Against that, a budget with millisecond granularity is not a tight budget; it is no budget at all.
- **Comparing reports audited under different allocations.** `profile_batch` rejects a mixed batch: excess figures computed against different budgets are not comparable, and the pooled bottleneck would be meaningless.

## Verification

- Allocation arithmetic: default budgets $\implies$ `allocated_budget_us == 25.0`, `unallocated_budget_us == 0.0`. `total_budget_us=20.0` $\implies$ `unallocated_budget_us == -5.0` and `is_overcommitted` `True` with a logged warning.
- Independent totals: with the allocation summing to 25 µs but `total_budget_us=15.0`, a trace of 1.5/2.0/7.0/3.5/4.0 µs (total 18.0) $\implies$ `is_within_budget` `False`, `breached_stages == []`, `budget_deficit_us == 3.0` — a breach with no stage at fault.
- Bottleneck definition: on that same compliant trace the per-stage headroom is −0.5/−1.0/−3.0/−1.5/−1.0 µs $\implies$ `primary_bottleneck_stage` is `INGRESS_NETWORK`, not `SIGNAL_COMPUTATION` (which v1.0.0 named, having ranked by raw duration). A tie at +1.0 µs between ingress and decode $\implies$ ingress, the earlier stage.
- Boundary: every stage exactly on its share $\implies$ `is_within_budget` `True` and `breached_stages == []`. Ingress at 2.000001 µs $\implies$ breach, while `total_tick_to_trade_latency_us` still displays 25.0 — confirming comparison precedes rounding.
- Amdahl bound: the 18.0 µs trace against a 10.0 µs budget $\implies$ `budget_deficit_us == 8.0` and `stage_reduction_required_fraction[SIGNAL_COMPUTATION] == 8/7 > 1.0`, with every stage above 1.0 $\implies$ no single stage can close this deficit.
- Percentile arithmetic against hand-derived ranks: over totals 1..100, nearest rank returns P50 = 50, P95 = 95, P99 = 99 $\implies$ every figure is an observed total. 50 samples at 1 µs plus 50 at 9 µs $\implies$ P50 = 1.0, never an interpolated 5.0.
- **Non-additivity of stage P99s**: 100 traces of 1/1/5/2/2 µs where five traces each add 8.0 µs to a *different* stage $\implies$ `p99_total_us == 19.0`, `sum_of_stage_p99_us == 11.0`, `comonotonic_gap_us == -8.0`. The same 100 traces with a single trace stalling in *every* stage at once $\implies$ gap 0.0.
- Sample sufficiency: 100 clean traces $\implies$ `is_p99_resolvable` `True`, status `LATENCY_BUDGET_HEALTHY`; 99 $\implies$ `False` and `LATENCY_BUDGET_INSUFFICIENT_SAMPLES`; 10 traces containing one 33 µs breach $\implies$ `LATENCY_BUDGET_BREACH` (a breach needs no resolution guarantee).
- Trace rejection: a trace missing any stage, or carrying a NaN, Inf, negative, boolean, non-numeric or implausibly large duration, or an unrecognised stage key, each raises rather than producing a report. All errors subclass `ValueError`.
- Config rejection: a partial `stage_sla_budgets` map (v1.0.0 gave the omitted stages an invented 10 µs each, turning a 2 µs budget into 42 µs), a zero/negative/non-finite stage budget, and a non-positive `total_budget_us` each raise `LatencyBudgetConfigError`. Mutating one engine's `sla_budgets` leaves the next engine's defaults at 2.0 µs.
- Run `python -m unittest discover -s skills/strategy-latency-budget-decomposition/scripts`.

## Related Skills

- `tick-to-trade-latency-measurement`
- `colocation-latency-budget-accounting`
- `latency-monitoring-percentile-based-slas`
- `model-inference-latency-budget-for-live-trading`
- `network-jitter-impact-on-strategy-performance`
- `risk-control-latency-budget`
- `hardware-timestamping-vs-software-timestamping-accuracy`
- `clock-synchronization-ptp-for-trading-hosts`
- `network-interface-level-tick-timestamping`
- `co-location-provider-selection-and-network-topology`
