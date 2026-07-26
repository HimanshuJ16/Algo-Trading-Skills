---
name: co-location-provider-selection-and-network-topology
description: >-
  Quantitative evaluation framework for scoring co-location data centers, modeling cross-venue fiber/microwave network topologies, and decomposing microsecond latency budgets.
domain: Infrastructure
subdomain: Network Architecture
tags: ["colocation", "latency-budget", "equinix-ny4", "cme-aurora", "slough-ld4", "network-topology"]
brokers_frameworks: ["Generic Infrastructure"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing ultra-low latency or cross-venue arbitrage infrastructure. Selecting the wrong co-location facility (e.g., placing an equities-futures arbitrage bot in Equinix NY4 Secaucus instead of CME Aurora IL or Carteret NJ) introduces unnecessary propagation delays and cross-connect overhead. This module mathematically models fiber vs. microwave propagation latencies, network switch hop delays, and facility costs to optimize server placement.

## Prerequisites

- Physical distance (km) between candidate co-location facilities and exchange matching engines.
- Link medium specification: Fiber (glass index $n \approx 1.5$, $\sim 5\,\mu\text{s/km}$) vs. Microwave (vacuum/air index $n \approx 1.0$, $\sim 3.33\,\mu\text{s/km}$).

## Workflow

1. **Topology Definition**: Define candidate data center nodes (e.g. `NY4_Secaucus`, `Carteret_NJ`, `CME_Aurora`, `Slough_LD4`) and targeted exchange matching engines.
2. **Latency Budget Decomposition**:
   - $\text{Propagation Delay} = \text{Distance (km)} \times \text{Speed Constant}$.
   - $\text{Hardware Hop Delay} = \text{Num Switches} \times \text{Switch Latency (ns)}$.
   - $\text{Cross-Connect Delay} = \text{Fiber Patch Delay}$.
   - $\text{Total One-Way Latency} = \text{Propagation} + \text{Hops} + \text{Cross-Connect}$.
3. **Total Cost of Ownership (TCO) Calculation**:
   - Aggregate monthly recurring costs: $\text{TCO} = \text{Rack Cost} + (\text{Power (kW)} \times \text{Rate}) + \text{Cross-Connect MRC}$.
4. **Multi-Attribute Utility Scoring**: Score facilities on weighted criteria (Latency Weight vs. Cost Weight).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Fiber Propagation Constants**: Assuming light travels in fiber optic cable at the speed of light in a vacuum ($c = 3 \times 10^8\text{ m/s}$). In silica fiber, $v \approx 2 \times 10^8\text{ m/s}$ ($5.0\,\mu\text{s/km}$).
- **Overlooking Jitter vs. Speed**: Selecting a microwave link with lower mean latency but massive variance during rain/storm events without fallback to fiber.
- **Ignoring Equal-Length Patching**: Failing to request equal-length fiber cross-connects inside shared data centers, leading to microsecond imbalances between strategy servers.

## Verification

- Instantiate `ColocationTopologyEvaluator`. Model a link between Secaucus NY4 and CME Aurora (approx 1,200 km). Compare fiber vs microwave propagation latencies. Verify that microwave propagation delivers $\sim 4.0\text{ms}$ vs fiber's $\sim 6.0\text{ms}$.
- Run `python scripts/test_co_location_provider_selection_and_network_topology.py`.

## Related Skills

- `cross-venue-latency-arbitrage-defensive-design`
- `microwave-vs-fiber-network-links-for-cross-market-latency`
