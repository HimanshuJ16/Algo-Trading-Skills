---
name: cross-strategy-shared-infrastructure-resource-contention
description: >-
  Quantitative infrastructure management engine for auditing resource contention across co-located strategies, isolating CPU core affinity, rate-limiting FIX gateways, and preempting low-priority tasks.
domain: Real-Time Infrastructure
subdomain: Shared Resource Management
tags: ["resource-contention", "cpu-affinity", "fix-rate-limiting", "preemption", "multi-strategy", "latency-jitter", "noisy-neighbor"]
brokers_frameworks: ["Linux Taskset", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-strategy trading environments where multiple trading algorithms share physical hardware, cloud instances, or shared FIX order gateways (e.g. HFT market-making running alongside EOD portfolio rebalancing). Shared hardware resources (CPU cores, L3 cache, memory bandwidth, FIX message rate limits) are vulnerable to "noisy neighbor" effects. This module monitors shared utilization metrics, isolates high-priority strategies using CPU affinity, and dynamically throttles low-priority tasks during resource contention spikes ($\ge 85\%$).

## Prerequisites

- Telemetry feed monitoring CPU utilization ($\% \text{CPU}$), Memory ($\% \text{RAM}$), and FIX gateway msg rate ($\text{msgs/sec}$).
- Priority classifications per strategy (`HIGH_HFT`, `MEDIUM_ARB`, `LOW_BATCH`).

## Workflow

1. **Strategy Telemetry Registration**: Ingest resource utilization metrics for all co-located strategies.
2. **Contention State Audit**:
   - $\text{Max Utilization} = \max(\% \text{CPU}, \% \text{RAM}, \frac{\text{FIX Rate}}{\text{Limit}} \times 100)$.
   - $\text{State} = \text{NORMAL}$ if $< 75\%$, $\text{ELEVATED}$ if $[75\%, 85\%)$, $\text{CRITICAL}$ if $\ge 85\%$.
3. **Preemption & Dynamic Throttling**:
   - Under `CRITICAL` state:
   - Pause or throttle `LOW_BATCH` strategies ($0\%$ quota).
   - Cap `MEDIUM_ARB` message rates to $50\%$ of baseline.
   - Reserve $100\%$ bandwidth and pinned CPU cores for `HIGH_HFT` market-making strategies.
4. **Audit Report Generation**: Output mitigation directives (`ContentionMitigationReport`).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unpinned Multi-Threaded Batch Jobs**: Running un-isolated batch data jobs that consume all physical CPU cores, causing 50ms latency spikes in co-located execution gateways.
- **Shared FIX Rate Limit Exceedance**: Allowing low-priority rebalancing strategies to burst order messages, breaching broker FIX rate limits and getting the entire session disconnected.
- **Ignoring NUMA Architecture**: Failing to pin CPU cores and memory channels on the same NUMA node, causing inter-socket bus latency penalties.

## Verification

- Instantiate `SharedInfrastructureContentionManager`. Register `HFT_MM` (`HIGH_HFT`, Core 1), `Arb_Desk` (`MEDIUM_ARB`, Core 2), and `Batch_Rebalance` (`LOW_BATCH`, Core 3). Simulate normal CPU usage ($45\%$) and verify state is `NORMAL`. Simulate CPU utilization spike ($90\%$). Verify manager triggers `CRITICAL` state, pauses `Batch_Rebalance`, and caps `Arb_Desk`.
- Run `python scripts/test_cross_strategy_shared_infrastructure_resource_contention.py`.

## Related Skills

- `cross-datacenter-clock-sync-validation`
- `cost-monitoring-for-cloud-trading-infrastructure`
---
