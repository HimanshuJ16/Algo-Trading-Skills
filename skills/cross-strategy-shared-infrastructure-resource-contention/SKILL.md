---
name: cross-strategy-shared-infrastructure-resource-contention
description: Advisory contention manager for co-located trading strategies sharing a
  host and a FIX gateway - classifies CPU, memory, and gateway-rate telemetry into
  contention states and emits hysteresis-gated preemption and throttling directives
  for a supervisor to enforce.
domain: Real-Time Infrastructure
subdomain: Shared Resource Management
tags:
- resource-contention
- cpu-affinity
- fix-rate-limiting
- preemption
- multi-strategy
- latency-jitter
- noisy-neighbor
brokers_frameworks:
- Linux Taskset
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-strategy environments where several trading algorithms share physical hardware, a cloud instance, or a FIX order-entry session (e.g. HFT market-making co-located with EOD portfolio rebalancing). Shared CPU cores, L3 cache, memory bandwidth, and per-session message-rate budgets are all vulnerable to "noisy neighbour" effects, and a message-rate breach is a shared failure: exceeding a venue's session throttle degrades or disconnects the session for *every* strategy on it, not just the offender.

`SharedInfrastructureContentionManager` ingests host and gateway telemetry, classifies the contention state from the single most-loaded resource, and returns a `ContentionMitigationReport` of mitigation directives.

## When NOT to Use

- **As an enforcement mechanism.** This module is an advisory control plane. It does not call `taskset`, suspend a process, cancel orders, or reconfigure a gateway - it labels processes and emits directives that your supervisor must enforce. Treating the report as if the mitigation already happened is the primary misuse.
- **As a kill switch.** Pausing a strategy is not the same as cancelling its orders. Emergency order cancellation is a separate, dedicated control (EU RTS 6 Article 12 kill functionality) - see `strategy-level-kill-switch-vs-portfolio-level-kill-switch`.
- **As a pre-trade risk control.** Load shedding must never suspend or degrade the pre-trade risk checks themselves (see Common Pitfalls).
- **For sub-millisecond reaction.** This is a sampled telemetry loop, not an inline hot-path guard. Tick-to-trade budgeting belongs in `strategy-latency-budget-decomposition`.

## Prerequisites

- Telemetry feed supplying host-normalised CPU % and RAM % in `[0, 100]` (divide multi-core `top`-style aggregates by the core count) and the aggregate outbound FIX rate in msgs/sec.
- The venue- or broker-negotiated message-rate ceiling for the shared session. This is **not** a FIX-protocol constant; it is allocated per session by the venue.
- A priority classification per strategy: `HIGH_HFT`, `MEDIUM_ARB`, or `LOW_BATCH`.
- A supervisor able to act on the directives (cgroup/cpuset changes, job suspension, gateway rate caps).

## Workflow

1. **Register strategies.** `register_process()` validates the priority class and rejects an unrecognised one. Do not skip this validation: a typo'd class would otherwise fall through every preemption branch, leaving that process neither throttled nor protected.
2. **Ingest telemetry and validate it.** Non-finite, negative, or un-normalised readings are rejected rather than scored. A NaN reading compares False against every threshold, so an unvalidated NaN silently reports `NORMAL` on a saturated host — the control fails open.
3. **Classify on the binding resource.** $\text{Max Utilisation} = \max(\%\text{CPU},\ \%\text{RAM},\ \frac{\text{FIX rate}}{\text{negotiated limit}} \times 100)$, never an average — averaging hides a saturated gateway behind an idle CPU. The report names the binding resource so the escalation is auditable.
   - `NORMAL` below 75%, `ELEVATED` in $[75\%, 85\%)$, `CRITICAL_CONTENTION` at $\ge 85\%$. These are operational defaults, not regulatory figures; calibrate them from your own capacity tests.
4. **Preempt under `CRITICAL_CONTENTION`.**
   - `LOW_BATCH` → `PAUSED`. Cancel or hand off any working orders the job owns *before* suspending it; a suspended process with resting orders manages nothing.
   - `MEDIUM_ARB` → `THROTTLED` to a concrete msgs/sec cap (`throttle_caps_msg_per_sec`), computed against a declared baseline rate where one is supplied and against the observed rate otherwise — the directive states which.
   - `HIGH_HFT` → protected, with its pinned core reported for verification.
5. **De-escalate with hysteresis, not on one clear sample.** Suppressed strategies are released only after `resume_clear_samples` consecutive samples strictly below `resume_threshold_pct` (defaults: 3 samples below 75%). At `ELEVATED`, existing suppression is *held*, not lifted. Resuming a batch job the instant utilisation dips below the critical line simply re-saturates the resource that triggered the pause.
6. **Act on the report.** Enforce every directive in `mitigation_directives`, then verify the effect on the next telemetry sample.

> Full procedure: see `references/workflows.md`.
> Standards and cited sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating `taskset` as isolation.** CPU affinity is a *restriction*, not a *reservation*: `taskset(1)` guarantees only that "the thread will not migrate to a CPU outside the new affinity mask" — it does not stop other threads, kernel work, or interrupts from landing on that core. Keeping neighbours off a core needs isolation (cgroup cpusets or `isolcpus`/`nohz_full`) plus IRQ affinity, with `taskset` used to move the trading thread *onto* the isolated core.
- **Shedding the risk controls as "low priority" load.** Pre-trade risk checks are not discretionary load. Under US SEC Rule 15c3-5 they must be under the broker-dealer's direct and exclusive control, and EU RTS 6 Article 15 requires maximum-message and order-value limits on entry. Degrade strategy throughput, never the checks in front of it.
- **Pausing a strategy and calling it flat.** Suspending a `LOW_BATCH` process freezes its logic but leaves its resting orders working and its positions unhedged. Cancel or reassign them first; kill functionality is a separate control.
- **Bursting a shared FIX session.** Venue throttles are per-session and shared. On CME iLink 3, crossing a Reject threshold gets subsequent messages rejected with a Business Level Reject until the rate falls back, and crossing the larger Terminate threshold terminates the session — taking every strategy on it down, not just the burster.
- **Resume flapping.** Auto-resuming preempted work on the first sub-threshold sample oscillates the host across the critical line and produces exactly the latency jitter the preemption was meant to prevent.
- **Ignoring NUMA topology.** Pinning a thread to a core whose memory is allocated on a different NUMA node pays an inter-socket penalty on every access; pin the thread and bind its memory to the same node (`numactl --cpunodebind --membind`).
- **Un-normalised CPU telemetry.** A raw 400% reading from a 4-core host would hold the manager in `CRITICAL_CONTENTION` permanently; it is rejected at the boundary instead.

## Verification

- Instantiate `SharedInfrastructureContentionManager()`. Register `HFT_MM` (`HIGH_HFT`, core 1), `StatArb` (`MEDIUM_ARB`, core 2), and `EOD_Report` (`LOW_BATCH`, core 3). At CPU 45% / RAM 50% / 360 msgs-sec against a 1000 msgs-sec limit, expect `NORMAL` and no suppression. At CPU 92%, expect `CRITICAL_CONTENTION`, `EOD_Report` paused, `StatArb` throttled, `HFT_MM` running, and `binding_resource == "CPU"`.
- With an idle host but 950 msgs/sec on a 1000 msgs/sec session, expect `CRITICAL_CONTENTION` with `binding_resource == "FIX_GATEWAY"` — a saturated gateway must escalate on its own.
- After a `CRITICAL_CONTENTION` sample, feed one 80% sample and confirm `EOD_Report` is still `PAUSED`; then confirm it resumes only on the third consecutive clear sample.
- Confirm a NaN CPU reading raises rather than reporting `NORMAL`.
- Run `python -m unittest discover -s skills/cross-strategy-shared-infrastructure-resource-contention/scripts`.

## Related Skills

- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
- `broker-side-order-throttle-detection`
- `feed-handler-cpu-pinning-and-numa-awareness`
- `cross-datacenter-clock-sync-validation`
- `cost-monitoring-for-cloud-trading-infrastructure`
