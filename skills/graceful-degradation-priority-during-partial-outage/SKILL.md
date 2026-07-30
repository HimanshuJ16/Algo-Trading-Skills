---
name: graceful-degradation-priority-during-partial-outage
description: >-
  Fault-tolerant trading architecture engine for managing multi-tier priority load shedding (P1 Risk/Cancel, P2 Exits, P3 Entries, P4 Analytics) during partial system outages.
domain: High-Availability Architecture
subdomain: Fault Tolerance & Load Shedding
tags: ["graceful-degradation", "load-shedding", "priority-queue", "partial-outage", "circuit-breaker", "high-availability", "fault-tolerance"]
brokers_frameworks: ["Resilience4j Pattern", "Python Dataclasses", "PriorityQueue"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in high-frequency trading infrastructure, microservice execution gateways, and real-time risk management platforms. During partial infrastructure degradation (50% network packet loss, database pool exhaustion, or market data vendor outages), trading systems must shed non-critical workloads to guarantee latency and reliability for critical risk operations. This module enforces a 4-tier priority hierarchy (**P1 Critical Risk/Cancels**, **P2 Exits**, **P3 Entries**, **P4 Analytics**), load-shedding lower priorities during partial or critical outages.

## Prerequisites

- Task classification tags (`P1_CRITICAL`, `P2_HIGH`, `P3_MEDIUM`, `P4_LOW`).
- System health status monitor (`cpu_utilization_pct`, `packet_loss_pct`, `db_latency_ms`).

## Workflow

1. **System Health & Mode Audit**:
   - Evaluate system health metrics:
     - `NORMAL_HEALTHY`: Packet loss $< 1\%$, CPU $< 75\%$.
     - `PARTIAL_DEGRADATION`: Packet loss $1\% - 10\%$ or CPU $75\% - 90\%$.
     - `CRITICAL_OUTAGE`: Packet loss $> 10\%$ or CPU $> 90\%$.
2. **Priority Load Shedding Filter**:
   - `NORMAL_HEALTHY` $\implies$ Process P1, P2, P3, P4.
   - `PARTIAL_DEGRADATION` $\implies$ Drop P4 (Analytics/Logs). Process P1, P2, and queue P3.
   - `CRITICAL_OUTAGE` $\implies$ Drop P4, P3, P2. Execute P1 Risk/Cancel operations ONLY (`CAPITAL_PRESERVATION_MODE`).
3. **Task Queue Execution**:
   - Process high-priority queues first using strict priority ordering.
4. **Audit Report Generation**: Output structured `GracefulDegradationAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Allowing Low-Priority Tasks to Block P1 Risk Controls**: Processing non-critical historical database writes on the main event loop, causing P1 MassCancel requests to time out.
- **Failing to Shed P4 Workloads Early**: Waiting for total system failure before dropping non-essential analytics logging.
- **Over-Shedding High-Priority Stop Exits**: Dropping P2 position exit orders during partial degradation, leaving positions unhedged during market crashes.

## Verification

- Instantiate `GracefulDegradationRouterEngine`. Test `NORMAL_HEALTHY` $\implies$ verifies all tasks P1-P4 are processed. Test `PARTIAL_DEGRADATION` (CPU=85%) $\implies$ verifies P4 analytics tasks are shed while P1/P2/P3 execute. Test `CRITICAL_OUTAGE` (Packet Loss=15%) $\implies$ verifies P2/P3/P4 are shed and only P1 MassCancel risk tasks execute in `CAPITAL_PRESERVATION_MODE`.
- Run `python scripts/test_graceful_degradation_router.py`.

## Related Skills

- `capital-preservation-mode-for-degraded-conditions`
- `execution-algorithm-kill-switch-integration`
---
