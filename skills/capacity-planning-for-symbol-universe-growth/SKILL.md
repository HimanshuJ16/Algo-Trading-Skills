---
name: capacity-planning-for-symbol-universe-growth
description: Quantitative infrastructure modeling tool to forecast CPU, memory, and
  network bandwidth requirements as a strategy's symbol universe expands.
domain: Infrastructure
subdomain: Scalability
tags:
- capacity-planning
- throughput
- bandwidth
- memory
- symbol-universe
brokers_frameworks:
- Generic Systems Engineering
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when preparing to scale a quantitative strategy from a small basket of symbols (e.g., 50 tech stocks) to a massive universe (e.g., Russell 3000 or global crypto pairs). The engine models the expected network bandwidth (Mbps), memory footprint (GB), and CPU core requirements based on peak market data tick rates, ensuring your infrastructure is scaled proactively to prevent latency spikes and dropped packets.

## Prerequisites

- Base knowledge of average and peak tick rates (messages per second) for the target asset class.
- Specifications of the hardware (e.g., NIC bandwidth, CPU processing capability per core in messages/second).

## Workflow

1. **Parameter Initialization**: Define the hardware constraints in `HardwareSpec` (e.g., 10 Gbps NIC, 500,000 msgs/sec per CPU core).
2. **Universe Definition**: Define the target `UniverseSpec` (e.g., 3000 symbols, 150 bytes per FIX/ITCH message, 500 ticks/sec/symbol at market open).
3. **Capacity Calculation**: The `CapacityPlanner` engine calculates the raw bandwidth required, the total RAM needed for order book maintenance, and the number of partitioned CPU cores needed to process the stream without queuing delay.
4. **Validation**: Check if the required bandwidth exceeds the NIC capacity or if the CPU core count exceeds the physical server limits.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Designing for Average Load**: Architecting for the *average* daily tick rate instead of the *peak* (market open/close) microbursts. This results in catastrophic latency during the most profitable trading periods.
- **Ignoring Protocol Overhead**: Calculating payload size (e.g., 50 bytes) but forgetting the TCP/IP and Ethernet frame overhead (~60-100 bytes per packet).
- **Single-Threaded Bottlenecks**: Assuming that buying a 64-core machine solves the problem, without writing partitioned, lock-free code that can actually utilize those cores.

## Verification

- Simulate a 5,000 symbol universe with 1,000 ticks/sec/symbol. The engine should correctly identify if a standard 1 Gbps link will choke (it will, requiring ~5+ Gbps).
- Run `python scripts/test_capacity_planning_for_symbol_universe_growth.py`.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `broker-side-order-throttle-detection`
