---
name: latency-monitoring-percentile-based-slas
description: >-
  Low-latency infrastructure SLA monitoring engine calculating microsecond-level P50, P90, P99, and P99.9 percentiles, monitoring latency jitter, and auditing tick-to-trade SLA breaches.
domain: Market Microstructure & Latency
subdomain: Latency Infrastructure & SLA Governance
tags: ["latency-monitoring", "percentiles", "p99", "p999", "sla-breach", "tick-to-trade", "jitter", "microsecond-sla"]
brokers_frameworks: ["HDR Histogram", "CLOCK_MONOTONIC_RAW", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when auditing low-latency algorithmic trading infrastructure, order gateways, and tick-to-trade execution pipelines. In high-frequency trading (HFT), simple average latency ($\bar{x}$) is a vanity metric that masks severe tail-latency spikes caused by garbage collection pauses, kernel interrupts, and ring buffer queue drops. This module measures high-resolution microsecond percentiles ($P_{50}, P_{90}, P_{95}, P_{99}, P_{99.9}$), tracks latency jitter, and enforces strict **Percentile-Based SLAs** ($P_{99} \le 200\ \mu\text{s}, P_{99.9} \le 1,000\ \mu\text{s}$).

## Prerequisites

- Latency sample series payload (`pipeline_stage`: `TICK_TO_TRADE` / `ORDER_GATEWAY`, `samples_microseconds`).
- SLA budget targets (`sla_p50_us`, `sla_p99_us`, `sla_p999_us`).

## Workflow

1. **Latency Sample Series Ingestion & Sort**:
   - Ingest microsecond latency timestamps and compute sample count $N$.
2. **High-Resolution Percentile Calculation**:
   - Calculate exact $P_{50}, P_{90}, P_{95}, P_{99}, P_{99.9}$ metrics.
   - Compute Latency Jitter ($\sigma_{\text{latency}}$ and Interquartile Range $IQR = P_{75} - P_{25}$).
3. **SLA Budget Compliance Audit**:
   - Audit $P_{50} \le \text{sla\_p50\_us}$.
   - Audit $P_{99} \le \text{sla\_p99\_us}$. If breached $\implies$ Trigger `SLA_BREACH_P99_WARNING`.
   - Audit $P_{99.9} \le \text{sla\_p999\_us}$. If breached $\implies$ Trigger `SLA_BREACH_P999_CRITICAL`.
4. **Audit Report Generation**: Output structured `LatencySlaReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on Average Latency**: Using average latency to measure HFT performance, completely missing 10-millisecond tail latency spikes that lose trades.
- **Averaging Percentiles Across Nodes**: Calculating average of $P_{99}$ across multiple servers, which is mathematically invalid and understates true system lag.
- **Suffering from Coordinated Omission**: Failing to record latency during queue backpressure stalls, under-reporting true tail latency.

## Verification

- Instantiate `LatencyPercentileSlaEngine`. Audit 10,000 Tick-to-Trade samples ($P_{50} = 35\ \mu\text{s}, P_{99} = 120\ \mu\text{s}, P_{99.9} = 450\ \mu\text{s}$) against SLA budget ($P_{99} \le 200\ \mu\text{s}$) $\implies$ verify `SLA_COMPLIANCE_APPROVED`. Audit Tail Spike dataset ($P_{99.9} = 2,500\ \mu\text{s} > 1,000\ \mu\text{s}$) $\implies$ verify `SLA_BREACH_P999_CRITICAL`.
- Run `python scripts/test_latency_monitoring_percentile_based_slas.py`.

## Related Skills

- `colocation-latency-budget-accounting`
- `tick-to-trade-latency-measurement`
---
