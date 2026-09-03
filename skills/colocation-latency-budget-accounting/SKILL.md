---
name: colocation-latency-budget-accounting
description: >-
  Use when profiling the in-host hot path of a co-located server, decomposing NIC
  ingress to NIC egress into nanosecond phases and auditing each trace against its stage
  budget. Everything outside the box is a different skill.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: latency-budget, tick-to-trade, hft, colocation, hardware-timestamping, sla, tail-jitter, clock-domain
  brokers_frameworks: "Generic Infrastructure; NumPy"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when profiling and auditing the "hot path" of a high-frequency trading (HFT) server in a co-located data centre. Tick-to-trade (T2T) latency is the elapsed time from hardware packet arrival at the NIC ($T_0$) to order byte transmission ($T_5$). This module decomposes T2T into discrete processing phases (NIC ingress → decode → alpha signal → pre-trade risk → binary encode → NIC egress), audits them against a total and per-phase budget, names the phase most over its budget, and reports tail jitter ($P_{99}$, $P_{99.9}$).

## When NOT to Use

- **For anything outside the box.** This skill starts at NIC ingress and stops at NIC egress. Cross-venue propagation, facility choice and cross-connects belong to `co-location-provider-selection-and-network-topology`; the end-to-end measurement harness belongs to `tick-to-trade-latency-measurement`.
- **As the instrumentation itself.** This is the *offline accounting* half. It consumes traces that some lock-free hot-path instrumentation already captured; it does not capture timestamps, and it must never be called from the hot path.
- **When the timestamps are not on one clock.** $T_0/T_5$ come from the NIC's PTP hardware clock (PHC); $T_1 \dots T_4$ typically come from `CLOCK_MONOTONIC`. Convert to a single time base *before* building a `HotPathTrace` — see Pitfalls. The engine rejects traces that go backwards, but it cannot detect a constant inter-domain offset.
- **As a regulatory timestamping record.** MiFID II RTS 25 governs *business clock* accuracy and granularity for reportable events against UTC. `CLOCK_MONOTONIC` is not UTC-traceable, and this module's relative nanosecond deltas are not a substitute for a compliant timestamping record. See `references/standards.md`.
- **For sub-100 sample batches.** Percentiles finer than the batch can resolve are interpolations, not measurements (see Pitfalls).

## Prerequisites

- Hardware timestamping enabled on the NIC. On Linux, request `SOF_TIMESTAMPING_RX_HARDWARE` / `SOF_TIMESTAMPING_TX_HARDWARE` via `SO_TIMESTAMPING`, and confirm driver support with `ethtool -T <iface>`.
- A high-resolution in-host timer for $T_1 \dots T_4$: `clock_gettime(CLOCK_MONOTONIC)`, or `rdtsc`. **`rdtsc` returns cycle counts, not nanoseconds** — converting requires a calibrated invariant-TSC frequency (invariant TSC is advertised by `CPUID.80000007H:EDX[8]`), and readings are only comparable across cores when the TSC is invariant and synchronised.
- A documented conversion between the NIC PHC domain and the in-host timer domain.
- NumPy.

## Units

All timestamps, phase durations and SLAs are **nanoseconds (ns)**, as Python `int`. Percentile outputs are floats rounded to 1 dp; `count` is the batch size. Nothing in this module is microseconds — a value pasted in as µs is off by 1,000x and will not be caught.

## Workflow

1. **Hot Path Instrumentation**: record high-resolution timestamps at each stage boundary into a fixed-size lock-free buffer — $T_0$ NIC ingress, $T_1$ decode complete, $T_2$ alpha signal evaluated, $T_3$ pre-trade risk complete, $T_4$ order encoded, $T_5$ NIC egress.
2. **Normalise to One Clock**: convert $T_0/T_5$ (NIC PHC) and $T_1 \dots T_4$ (`CLOCK_MONOTONIC`) onto a single monotonic nanosecond base before constructing traces.
3. **Ingest with Quarantine**: build `HotPathTrace` objects off the hot path. Construction raises `ValueError` on non-monotonic timestamps — catch it **per trace**, increment a quarantine counter, and continue. A non-monotonic trace is an instrumentation or clock-domain defect, not a fast execution; do not clamp it to zero and do not let it abort the batch.
4. **Configure the Budget** (`LatencyBudgetAccountingEngine`): `total_sla_ns` plus a `phase_slas_ns` map covering **every** phase in `PHASE_NAMES`. Unknown or missing keys raise — a typo must not silently give a phase a 0 ns budget. If the phase budgets sum above the total, the engine logs a warning: that configuration is legal but means a trace can breach the total with no phase over its own budget.
5. **Audit** (`audit_trace`): `is_sla_breach` is a strict `>`, so a trace landing exactly on `total_sla_ns` is inside budget. On breach the report carries `phase_excess_ns` (duration − budget, per phase) and names as `primary_bottleneck_phase` the phase with the greatest excess — even when every excess is negative, in which case it is the phase closest to its limit. Ties break by hot-path order.
6. **Tail Reporting** (`compute_percentiles`): $P_{50}, P_{95}, P_{99}, P_{99.9}$ plus `mean` and `count` for the total and every phase. Read `count` before trusting a tail figure.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mixing Clock Domains**: NIC hardware timestamps are taken in the NIC's own PHC domain; `CLOCK_MONOTONIC` is a different clock. Subtracting one from the other yields an offset, not a duration — often large enough to be negative, sometimes small enough to look plausible. The engine rejects the negative case; the plausible case is silently wrong, which is why the conversion must be documented rather than assumed.
- **Using `time.time()` for Hot-Path Timestamps**: `time.time()` reads `CLOCK_REALTIME`, which NTP steps and slews — it can move backwards, so a phase can measure negative. Use `CLOCK_MONOTONIC` for in-host deltas and NIC hardware timestamps for $T_0/T_5$. A userspace timestamp also includes interrupt-handling and scheduler delay that the NIC timestamp excludes; that gap is not bounded and grows exactly when the machine is busiest.
- **Profiling in the Hot-Path Thread**: writing log lines or computing percentiles inside the execution thread adds I/O and allocation latency to the order it is measuring. Record into a lock-free ring buffer and run this module out-of-band.
- **Reading a P99.9 off a Small Batch**: a percentile finer than $1/(1-q)$ samples is interpolated between the top two observations. P99.9 from 10 samples is a restatement of the maximum with a decimal point on it. `compute_percentiles` warns and returns `count` — check it before putting the figure on a dashboard.
- **Believing a Breach Has No Bottleneck**: whenever the per-phase budgets sum above the total, every phase can be inside its own budget while the total breaches. A breached trace always names a phase; a *negative* `phase_excess_ns` on that phase is the signal that the budget allocation, not the code, is the problem.
- **Focusing on Mean Latency**: a 10 µs mean is meaningless if $P_{99.9}$ spikes to 5 ms during a volatility burst — the spike is exactly when the fill mattered. Evaluate SLAs on tails.
- **Silently Dropping Rejected Traces**: quarantining a bad trace without counting it biases the tail downwards, because instrumentation defects correlate with the slow path. Count and alert on the quarantine rate.

## Verification

- Construct `LatencyBudgetAccountingEngine(total_sla_ns=10000)` (default phase budgets). Audit a trace with phase durations `[1000, 1000, 8000, 1000, 4000]` ns: total is 15,000 ns, `is_sla_breach` is `True`, and `primary_bottleneck_phase` is `signal_to_risk_ns` with `phase_excess_ns["signal_to_risk_ns"] == 6500` (8,000 ns against a 1,500 ns budget).
- Construct `LatencyBudgetAccountingEngine(total_sla_ns=5000)` — the default phase budgets sum to 8,000 ns, so the engine warns. Audit a trace of five 1,100 ns phases: total 5,500 ns breaches, yet every phase is inside its own budget. `primary_bottleneck_phase` must still be non-`None`, with a negative excess.
- `HotPathTrace("BAD", 1000, 900, 2000, 3000, 4000, 5000)` must raise `ValueError`, not produce a −100 ns phase.
- Run `python -m unittest discover -s skills/colocation-latency-budget-accounting/scripts`.

## Related Skills

- `tick-to-trade-latency-measurement`
- `strategy-latency-budget-decomposition`
- `clock-drift-monitoring-alerting-thresholds`
- `hardware-timestamping-vs-software-timestamping-accuracy`
- `co-location-provider-selection-and-network-topology`
