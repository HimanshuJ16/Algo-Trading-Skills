---
name: microwave-vs-fiber-network-links-for-cross-market-latency
description: >-
  Cross-market network latency and link arbitration engine, evaluating microwave vs fiber light propagation physics, speed advantages, and automated rain fade failover routing.
domain: Market Microstructure Latency
subdomain: Cross-Market Latency & Wireless Infrastructure Optimization
tags: ["microwave-link", "fiber-optic", "cross-market-latency", "propagation-speed", "rain-fade", "chicago-to-nj", "line-of-sight", "hft-infrastructure"]
brokers_frameworks: ["CME Aurora", "NJ Secaucus / Carteret Data Centers", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when engineering cross-market latency pipelines (e.g. Chicago CME Aurora to New Jersey Secaucus/Carteret NASDAQ/NYSE). Signals travel at speed of light in air ($c_{\text{air}} \approx 299,700\text{ km/s}$) via line-of-sight **Microwave/Millimeter-Wave Links**, compared to silica glass fiber cables ($c_{\text{fiber}} \approx 203,945\text{ km/s}$). Microwave networks offer $\sim 33\%$ latency reduction ($\sim 8.0\text{ ms}$ RTT vs $\sim 14.0\text{ ms}$ RTT Chicago-to-NJ). However, microwave signals suffer from rain fade during heavy precipitation. This module calculates exact propagation physics, measures latency savings, and executes automated rain fade failover to backup fiber.

## Prerequisites

- Microwave link spec (`link_id`, `distance_km`, `propagation_speed_km_s`: e.g. 299,700, `bandwidth_mbps`).
- Fiber optic backup spec (`link_id`, `distance_km`, `propagation_speed_km_s`: e.g. 203,945, `bandwidth_mbps`).
- Real-time link telemetry (`current_weather`, `packet_loss_pct`, `snr_db`).

## Workflow

1. **Light Propagation Physics Calculation**:
   - Compute One-Way Propagation Latency:
     $$\tau_{\text{prop\_ms}} = \frac{\text{Distance}_{\text{km}}}{\text{Speed}_{\text{km/s}}} \times 1,000.0$$
   - Compute Round-Trip Time: $\text{RTT}_{\text{ms}} = 2 \times \tau_{\text{prop\_ms}}$.
2. **Latency Advantage Computation**:
   - Calculate latency delta: $\Delta \text{RTT}_{\text{ms}} = \text{RTT}_{\text{fiber}} - \text{RTT}_{\text{microwave}}$.
   - Compute percentage speed improvement: $\text{Adv}_{\%} = \frac{\Delta \text{RTT}}{\text{RTT}_{\text{fiber}}} \times 100.0\%$.
3. **Rain Fade & Telemetry Audit**:
   - If `current_weather == 'HEAVY_RAIN'` or `packet_loss_pct > 1.0%` $\implies$ Execute automated failover to Fiber (`FAILOVER_TO_FIBER_RAIN_FADE`).
   - If clear weather and packet loss $\le 0.1\% \implies$ Route over primary Microwave (`ROUTE_MICROWAVE_PRIMARY`).
4. **Audit Report Generation**: Output structured `NetworkLinkReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Rain Fade Degradation**: Continuing to route cross-market arbitrage orders over degraded microwave links during heavy storms, suffering packet drops and out-of-order execution.
- **Assuming Straight-Line Fiber Routes**: Assuming fiber optic cables travel in straight lines; fiber routes follow highway/rail rights-of-way, adding 15-20% extra physical distance.
- **Lacking Automated Failover**: Manual link switching during weather degradation, causing execution outages during volatile market events.

## Verification

- Instantiate `NetworkLinkArbitratorEngine`. Model Chicago-to-NJ corridor (Microwave: $1,200\text{ km}$ at $299,700\text{ km/s} \implies \text{RTT} = 8.01\text{ ms}$; Fiber: $1,350\text{ km}$ at $203,945\text{ km/s} \implies \text{RTT} = 13.24\text{ ms}$) $\implies$ verify $5.23\text{ ms}$ latency savings ($39.5\%$ faster), status `ROUTE_MICROWAVE_PRIMARY` in clear weather, and `FAILOVER_TO_FIBER_RAIN_FADE` during heavy rain.
- Run `python scripts/test_microwave_vs_fiber_network_links_for_cross_market_latency.py`.

## Related Skills

- `co-location-provider-selection-and-network-topology`
- `tick-to-trade-latency-measurement`
---
