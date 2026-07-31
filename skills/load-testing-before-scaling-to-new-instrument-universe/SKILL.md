---
name: load-testing-before-scaling-to-new-instrument-universe
description: >-
  Pre-scaling infrastructure capacity planning and load testing engine, projecting tick message throughput, L2 order book memory footprints, and network bandwidth utilization before expanding instrument universes.
domain: Data Management Global
subdomain: Infrastructure Capacity & System Scalability
tags: ["load-testing", "universe-scaling", "throughput", "memory-footprint", "network-bandwidth", "capacity-planning", "hft-infrastructure"]
brokers_frameworks: ["k6 / Gatling Load Benchmarks", "Prometheus / Grafana Observability", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when expanding trading strategies to a larger instrument universe (e.g., scaling from 50 S&P 500 stocks to 3,000 Russell 3000 stocks or 50,000 global equities/options). Expanding universe size exponentially increases tick message throughput (msg/sec), L2 order book memory consumption (GB), network bandwidth (Mbps), and database write IOPS. Operating without pre-scaling load testing causes memory exhaustion, dropped WebSocket packets, and system crashes during high-volatility market open events.

## Prerequisites

- Universe scale specification (`current_universe_size`, `target_universe_size`, `avg_ticks_per_sec_per_symbol`, `peak_multiplier`, `bytes_per_tick`, `mb_per_orderbook`).
- Hardware capacity specification (`available_ram_gb`, `max_network_mbps`, `max_db_iops`, `max_cpu_cores`).

## Workflow

1. **Peak Load & Throughput Projection**:
   - Compute peak message throughput:
     $$\text{Msg/sec}_{\text{peak}} = N_{\text{target}} \times \text{AvgTicksPerSec} \times \text{PeakMultiplier}$$
   - Compute required L2 order book memory footprint:
     $$\text{RAM}_{\text{req\_gb}} = \frac{N_{\text{target}} \times \text{MB\_per\_book} \times 1.25}{1024}$$
   - Compute peak network bandwidth:
     $$\text{Bandwidth}_{\text{mbps}} = \frac{\text{Msg/sec}_{\text{peak}} \times \text{BytesPerTick} \times 8}{1,000,000}$$
2. **Infrastructure Capacity & Resource Utilization Audit**:
   - Compute resource utilization percentages: $\text{RAM}_{\text{util\%}}, \text{Net}_{\text{util\%}}, \text{IOPS}_{\text{util\%}}$.
3. **Pre-Scaling Safety Threshold Audit**:
   - If any resource utilization $> 80.0\% \implies$ Trigger `LOAD_TEST_FAILED_CAPACITY_EXCEEDED`.
   - If all resources $\le 80.0\% \implies$ Approve `LOAD_TEST_PASSED_READY_TO_SCALE`.
4. **Audit Report Generation**: Output structured `LoadTestReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing Under Average Off-Peak Ticks**: Sizing servers for average 1 tick/sec instead of market-open peak 10x volatility spikes.
- **Ignoring L2 Order Book Memory Overhead**: Memory leakage from maintaining thousands of dynamic order book depth structures simultaneously.
- **Relying on Cached Load Test Results**: Using synthetic benchmarks that hit local Redis caches instead of testing true end-to-end database write IOPS.

## Verification

- Instantiate `InfrastructureLoadTesterEngine`. Audit scaling from 50 to 500 stocks (Peak Msg Rate $= 25,000\text{ msg/sec}$, RAM $= 12.2\text{ GB} \le 64\text{ GB}$, Net $= 64\text{ Mbps} \le 1000\text{ Mbps}$) $\implies$ verify `LOAD_TEST_PASSED_READY_TO_SCALE`. Audit scaling to 5,000 stocks (RAM Req $= 122\text{ GB} > 64\text{ GB}$) $\implies$ verify `LOAD_TEST_FAILED_MEMORY_EXCEEDED`.
- Run `python scripts/test_infrastructure_load_tester.py`.

## Related Skills

- `historical-tick-data-storage-and-compaction`
- `cross-region-data-replication-lag-monitoring`
---
