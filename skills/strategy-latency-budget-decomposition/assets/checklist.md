# Pre-Flight Checklist — Strategy Latency Budget Decomposition

All values in **microseconds**. Sign off before a stage budget is used to gate a
deployment, an optimisation decision, or a go-live.

## Budget derivation

- [ ] Is the end-to-end `total_budget_us` derived from the **opportunity** (signal decay,
      race duration, tolerable staleness) rather than from what the current code happens
      to achieve, and is that derivation written down?
- [ ] Is the total set **independently** of the sum of stage budgets, so over- and
      under-commitment are visible?
- [ ] If the allocation sums above the total, has the resulting warning been acknowledged
      and the over-commitment accepted deliberately?
- [ ] If it sums below the total, is the unallocated headroom a deliberate safety margin
      rather than budget nobody owns?
- [ ] Has every shipped default (2/3/10/5/5 µs) been replaced or explicitly re-endorsed?
      **Nobody publishes per-stage tick-to-trade budgets** — these are placeholders.

## Measurement

- [ ] Is a duration captured for **all five** stages of every trace, with incomplete
      traces quarantined rather than zero-filled?
- [ ] Are all boundary timestamps in **one clock domain** (no NIC-PHC minus
      `CLOCK_MONOTONIC` subtraction), and is `time.time()` excluded?
- [ ] Is the timer able to resolve the smallest stage being budgeted? An RTS 25-compliant
      *business* clock (100 µs divergence, 1 µs granularity) cannot measure a 2 µs stage.
- [ ] Is the cost of the instrumentation itself budgeted, given that every boundary read
      executes inside the path under audit?
- [ ] Are stages reporting `0.0` µs investigated as a timer-resolution finding rather than
      recorded as free?
- [ ] Is the audit running **off** the hot path?

## Trace hygiene

- [ ] Is `LatencyBudgetError` caught per trace so one bad capture cannot abort the batch?
- [ ] Is the **quarantine rate counted and alerted on**? Silently dropped traces bias the
      tail downwards, because instrumentation defects correlate with the slow path.

## Reading the verdict

- [ ] Is `primary_bottleneck_stage` read as "furthest over its allocated share" — and is a
      *negative* excess on the named stage understood to mean nothing was over budget?
- [ ] Before assigning optimisation work, has `stage_reduction_required_fraction` been
      checked? A value **≥ 1.0** means that stage cannot close the deficit even if
      deleted entirely.
- [ ] Has `stage_share_of_total` been used to confirm the targeted stage is actually large
      enough to matter?
- [ ] Is it understood that `is_within_budget` and `breached_stages` are independent —
      either can fire without the other?

## Tail profiling

- [ ] Are at least **100 traces** in the batch before any P99 is treated as an approval?
      Below that the status is `LATENCY_BUDGET_INSUFFICIENT_SAMPLES` and *no breach
      observed* is not *within budget*.
- [ ] Is the end-to-end tail read from **`p99_total_us`** and never assembled by summing
      `stage_p99_us`? Summing per-stage P99s **under**-states the total when stages stall
      independently.
- [ ] Has `comonotonic_gap_us` been interpreted? ≈ 0 → one shared stall cause; > 0 →
      independent stalls, sum over-provisions; **< 0 → stage-by-stage budgeting would
      approve a pipeline already over its end-to-end P99.**
- [ ] Are all reports in the batch produced under the **same allocation**?
- [ ] Has `breach_rate` been checked alongside the P99, so breaches rarer than 1-in-100
      are not hidden beneath the audited percentile?

## After changes

- [ ] Has the allocation been re-derived after any change to the strategy, venue,
      instrument, or hot-path code — rather than carried forward?
