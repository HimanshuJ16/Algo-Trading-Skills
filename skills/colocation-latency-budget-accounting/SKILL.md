---
name: colocation-latency-budget-accounting
description: Quantitative co-location telemetry module for decomposing tick-to-trade
  (T2T) latency budgets into nanosecond phases, detecting SLA breaches, and computing
  P99/P99.9 tail jitter statistics.
domain: Infrastructure
subdomain: Latency Optimization
tags:
- latency-budget
- tick-to-trade
- hft
- colocation
- hardware-timestamping
- sla
- tail-jitter
brokers_frameworks:
- Generic Infrastructure
- NumPy
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when profiling and auditing the "hot path" of high-frequency trading (HFT) servers in co-located data centers. Tick-to-trade (T2T) latency represents the elapsed time from hardware packet arrival at the NIC (`T_ingress`) to order byte transmission (`T_egress`). This module decomposes total T2T latency into discrete processing phases (NIC Ingress -> Decode -> Alpha Signal -> Risk Check -> FIX/Binary Encode -> NIC Egress) to identify software/hardware bottlenecks and track tail jitter ($P_{99}, P_{99.9}$).

## Prerequisites

- Hardware timestamping enabled on Network Interface Cards (NICs).
- Nanosecond or microsecond timer calls (e.g. `clock_gettime(CLOCK_MONOTONIC)` or `rdtsc` instruction).

## Workflow

1. **Hot Path Instrumentation**: Log high-resolution timestamps at key processing stage boundaries:
   - $T_0$: NIC Ingress Timestamp
   - $T_1$: Data Decode Completed
   - $T_2$: Alpha Signal Evaluated
   - $T_3$: Pre-Trade Risk Check Completed
   - $T_4$: Binary Order Encoded
   - $T_5$: NIC Egress Timestamp
2. **Phase Breakdown**: Calculate duration for each phase: $\Delta_i = T_i - T_{i-1}$.
3. **SLA Audit & Bottleneck Identification**: Compare phase durations against target SLAs. If $T_5 - T_0 > \text{Total\_SLA}$, flag the execution as a breach and isolate the phase responsible for the maximum excess delay.
4. **Tail Latency Reporting**: Calculate $P_{50}$, $P_{95}$, $P_{99}$, and $P_{99.9}$ metrics across executions to measure jitter.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Software Timestamps**: Measuring T2T latency using standard OS `time.time()`, which suffers from 1-10 microsecond interrupt jitter. Hardware NIC timestamps (`ethtool -T`) must be used for ingress/egress.
- **Profiling in Main Thread**: Writing log lines or computing percentiles directly in the hot-path execution thread, adding milliseconds of I/O latency to the order. Timestamps must be recorded in lock-free ring buffers and processed out-of-band.
- **Focusing Only on Mean Latency**: Relying on average T2T latency instead of $P_{99.9}$ tail latency. A 10µs mean latency is useless if $P_{99}$ spikes to 5ms during market volatility.

## Verification

- Instantiate `LatencyBudgetAccountingEngine` with a total SLA of 5,000 ns. Record 1,000 execution samples where Risk Check is artificially delayed to 4,000 ns (SLA 500 ns). Verify that the engine correctly flags SLA breaches and identifies `risk_check` as the primary bottleneck.
- Run `python scripts/test_colocation_latency_budget_accounting.py`.

## Related Skills

- `tick-to-trade-latency-measurement`
- `clock-drift-monitoring-alerting-thresholds`
