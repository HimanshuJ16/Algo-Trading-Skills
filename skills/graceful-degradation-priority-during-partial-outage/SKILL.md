---
name: graceful-degradation-priority-during-partial-outage
description: >-
  Use when a trading system is degraded but not dead and something has to be given up — decides, per task, whether to process it, defer it, or drop it under a four-tier priority hierarchy (P1 risk/cancel, P2 exits, P3 entries, P4 analytics), fails safe on unreadable health telemetry, and never sheds P1.
domain: High-Availability Architecture
subdomain: Fault Tolerance & Load Shedding
tags: ["graceful-degradation", "load-shedding", "priority-queue", "partial-outage", "capital-preservation", "high-availability", "fault-tolerance"]
brokers_frameworks: ["Generic Fault-Tolerant Architecture", "Google SRE Criticality / Load-Shedding Pattern", "MiFID II RTS 6 (EU 2017/589)", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a trading system is degraded but still running — packet loss on the venue link, CPU saturation on the gateway host, a database connection pool backing up — and the system must decide *which work it is going to stop doing* before the degradation decides for it. It classifies every pending task into a four-tier hierarchy (**P1 risk checks / mass-cancels / heartbeats**, **P2 exits and stop-losses**, **P3 new entries and child slices**, **P4 analytics and logging**) and returns a disposition per task: process, defer, or drop.

The engine is a **decision** engine, not a scheduler. It answers "what should be shed right now, and who has to be told"; dispatching, re-queuing and discarding remain the caller's job. Reach for it when you have a chokepoint that all work passes through and you would rather shed deliberately than discover your queue library's default under load.

## When NOT to Use

- **As a kill switch.** Shedding P3/P4 does not cancel resting orders or flatten positions. The kill path is `execution-algorithm-kill-switch-integration`; this engine decides that the kill task runs *first*, not what it does.
- **For a momentary burst.** A spike a bounded buffer absorbs is a buffering problem — see `backpressure-drop-degrade-policy` and `tick-buffering-burst-handling`. Load shedding is for sustained degradation.
- **When the data is wrong rather than the system slow.** Stale ticks, sequence gaps and crossed books call for `graduated-response-to-data-quality-degradation`; healthy CPU and clean packets can still carry garbage prices.
- **Across processes.** Mode state is in memory and per-instance. Two gateway processes each hold their own latched mode and recover independently; run one router per chokepoint or promote the mode to shared storage.
- **As the alternative arrangement itself.** MiFID II RTS 6 Art. 14(2)(g) requires arrangements to manage outstanding orders and positions during a disruption. This engine *reports* that P1/P2 work was shed (`manual_intervention_required`); the manual desk, broker phone line or DR runbook that acts on it is a separate control — see `disaster-recovery-runbook-for-full-region-outage`.

## Prerequisites

- Every task tagged with exactly one of `P1_CRITICAL`, `P2_HIGH`, `P3_MEDIUM`, `P4_LOW`. Untagged or mis-tagged tasks are rejected, not guessed at.
- A health sampler producing `cpu_utilization_pct` and `network_packet_loss_pct`, passing `None` (not `0.0`) for a metric it could not read.
- Thresholds calibrated to *your* hosts. The CPU/packet-loss defaults are illustrative; the DB-latency and sample-age checks ship **disabled** because no authority publishes a figure at which a trading system must enter capital preservation (see `references/standards.md`).
- A route for deferred P1/P2 work that does not depend on the degraded component.

## Workflow

1. **Classify the health sample** (`determine_system_mode`, pure and side-effect free):
   - `CRITICAL_OUTAGE`: packet loss $\ge 10\%$, CPU $\ge 90\%$, or a configured DB-latency/sample-age limit breached.
   - `PARTIAL_DEGRADATION`: packet loss $\ge 1\%$ or CPU $\ge 75\%$.
   - `NORMAL_HEALTHY`: otherwise. **Comparisons are inclusive** — a sample sitting exactly on a threshold degrades.
   - **Decision point — an unreadable metric is not a healthy metric.** `None` on any metric the configuration depends on escalates straight to `CRITICAL_OUTAGE`; a non-finite value raises. A `NaN` compares `False` against every threshold, so an unguarded one reads as perfect health and silently disables shedding.

2. **Apply recovery damping** (inside `process_and_filter_tasks`, which is therefore stateful):
   - Escalation is immediate; recovery is not. The engine steps down **one** severity level after `recovery_confirmation_samples` consecutive healthier samples, so `CRITICAL_OUTAGE → NORMAL_HEALTHY` is never one hop.
   - **Decision point — do not restore full load the instant a metric dips back under its threshold.** A system that has just crossed back below its overload point is not recovered; restoring the shed load re-enters the overload immediately.

3. **Route each task through the policy matrix**:

   | | P1 risk/cancel | P2 exits | P3 entries | P4 analytics |
   |---|---|---|---|---|
   | `NORMAL_HEALTHY` | process | process | process | process |
   | `PARTIAL_DEGRADATION` | process | process | **defer** | **drop** |
   | `CRITICAL_OUTAGE` | process | **defer** | **drop** | **drop** |

   - **Decision point — defer and drop are different answers.** A deferred exit is an open position that still has to be managed; a dropped tick log is gone and nobody cares. P2 is never dropped, and P3 is dropped rather than deferred in a critical outage because replaying an entry signal after recovery executes stale alpha at a price that no longer exists.
   - Shedding is **monotone in priority**: a tier is only shed once every lower tier is already shed. A custom `policy` that inverts this, or that sheds P1 in any mode, is rejected at construction.

4. **Dispatch in the returned order**: `processed_task_ids` is sorted P1-first (input order preserved within a tier), so a caller that dispatches in list order dispatches risk work first.

5. **Escalate and log**: if `manual_intervention_required` is set, P1/P2 work was shed or telemetry was unreadable — page a human and invoke the alternative arrangement. Log `audit_notes` and `classification_reasons` verbatim; mode transitions are logged with their before/after state.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading a missing metric as a healthy one.** A monitoring agent that dies mid-outage returns `NaN`, `None` or a frozen last value. `NaN >= threshold` is `False` for every threshold, so the router concludes the system is perfectly healthy and stops shedding at exactly the moment shedding matters. Reject non-finite values, pass `None` for unreadable ones, and stamp every sample with an age.
- **Trusting a stale health snapshot.** The health pipeline is one of the things that degrades. Without `sample_age_seconds` the router will happily route full load against a five-minute-old "everything is fine" reading.
- **Silently shedding a mis-tagged task.** A priority string of `"P1-CRITICAL"` instead of `"P1_CRITICAL"` matched no tier in the original routing, so during a critical outage the mass-cancel it carried was shed as if it were analytics. Unparseable priorities must raise and reject the batch, never fall through to an `else`.
- **Treating "shed" as "handled".** Counting a shed stop-loss exit as a successful load-shedding event means the position is still open, unhedged, and nobody has been told. Separate defer from drop, and escalate any P1/P2 that did not run.
- **Flapping the mode.** Metrics oscillating around 75% CPU toggle shedding on and off every sample, and each restoration of full load re-triggers the degradation. Damp the recovery, not the escalation.
- **Blocking P1 behind bulk work.** Processing historical tick writes on the same event loop as the mass-cancel path means P1 times out while P4 succeeds — priority routing only helps if the tiers do not share a bottleneck.
- **Replaying a deferred entry blindly.** Deferred tasks carry no freshness guarantee. Re-check the signal, the price and the position before executing anything that was queued during a degradation, and stagger the replay — flushing a deferred backlog at once is how a recovering system is knocked straight back over.
- **Assuming the priority hierarchy is a regulatory requirement.** It is an engineering design. RTS 6 contains no prioritisation clause (see `references/standards.md`); what it does require is that outstanding orders and positions still get managed.

## Verification

- Instantiate `GracefulDegradationRouterEngine()` and route one task per tier. `NORMAL_HEALTHY` (CPU $40\%$, loss $0.1\%$) $\implies$ all four processed. `PARTIAL_DEGRADATION` (CPU $85\%$) $\implies$ P1/P2 processed, P3 deferred, P4 dropped. `CRITICAL_OUTAGE` (loss $15\%$) $\implies$ P1 processed, P2 deferred, P3/P4 dropped, `manual_intervention_required` true.
- Boundary checks: CPU exactly $75.0\%$ and packet loss exactly $1.0\%$ degrade; $74.999\%$ does not; packet loss exactly $10.0\%$ and CPU exactly $90.0\%$ are critical.
- Fail-safe checks: `cpu_utilization_pct=float('nan')` raises `InvalidHealthMetricError`; `cpu_utilization_pct=None` yields `CRITICAL_OUTAGE`; with `max_health_sample_age_seconds=5.0`, a sample aged $5.0\text{s}$ yields `CRITICAL_OUTAGE` even when every metric reads healthy.
- Negative checks: `TradingTask("T1", "MASS_CANCEL", "P1-CRITICAL")` raises `UnknownTaskPriorityError`; a policy that sheds P1, or that sheds P2 while processing P3, raises `LoadSheddingConfigurationError`.
- Recovery: after a critical outage, three consecutive healthy samples step the mode to `PARTIAL_DEGRADATION`, not to `NORMAL_HEALTHY`; one bad sample resets the streak.
- Run `python -m unittest discover -s skills/graceful-degradation-priority-during-partial-outage/scripts`.

## Related Skills

- `capital-preservation-mode-for-degraded-conditions`
- `execution-algorithm-kill-switch-integration`
- `backpressure-drop-degrade-policy`
- `graduated-response-to-data-quality-degradation`
- `graceful-degradation-to-polling-fallback`
- `disaster-recovery-runbook-for-full-region-outage`
- `chaos-engineering-for-trading-infrastructure`
