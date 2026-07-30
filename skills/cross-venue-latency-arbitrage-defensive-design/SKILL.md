---
name: cross-venue-latency-arbitrage-defensive-design
description: Quantitative HFT market-making module for detecting cross-venue latency
  arbitrage, calculating micro-price toxicity, and executing defensive quote pulls,
  spread widening, and size reductions.
domain: Market Microstructure & HFT
subdomain: Latency Arbitrage & Adverse Selection
tags:
- latency-arbitrage
- hft
- market-making
- micro-price
- stale-quote
- adverse-selection
- cross-venue
brokers_frameworks:
- Order Book Engine
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when co-locating market-making algorithms across geographically separated exchanges (e.g. CME Chicago `ES` futures vs. Nasdaq Carteret `SPY` ETF). When a price jump occurs on the primary venue, ultra-fast HFT arbitrageurs send aggressive sweep orders to pick off stale quotes on secondary venues before local quote cancellations arrive. This module computes real-time micro-price toxicity ($\tau_{\text{toxicity}}$), monitors cross-venue latency margins, and triggers defensive quote cancellations, dynamic spread widening, and order size reductions.

## Prerequisites

- Primary lead venue order book feed ($P_{\text{bid}}, P_{\text{ask}}, V_{\text{bid}}, V_{\text{ask}}$).
- Cross-venue network RTT latencies ($t_{\text{cancel\_rtt}}$, $t_{\text{hft\_latency}}$).

## Workflow

1. **Lead Venue Micro-Price & Toxicity Audit**:
   - Compute Micro-Price: $P_{\text{micro}} = \frac{V_{\text{ask}} P_{\text{bid}} + V_{\text{bid}} P_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$.
   - Compute Toxicity Index $\tau_{\text{toxicity}} = \left|\frac{P_{\text{micro}} - P_{\text{mid}}}{\text{TickSize}}\right|$.
2. **Cross-Venue Latency Margin Audit**:
   - $\Delta t_{\text{margin}} = (t_{\text{lead\_event}} + t_{\text{hft\_latency}}) - (t_{\text{cancel\_sent}} + t_{\text{cancel\_rtt}})$.
   - If $\Delta t_{\text{margin}} < 0 \implies$ High Pick-Off Risk!
3. **Defensive Quote Adjustment**:
   - If $\tau_{\text{toxicity}} \ge 2.0$ or $\Delta t_{\text{margin}} < 0$:
   - Trigger **Preemptive Quote Cancel** on secondary venue.
   - Widen Quote Spread: $S_{\text{defensive}} = S_{\text{base}} \times (1.0 + k \cdot \tau_{\text{toxicity}})$.
   - Reduce Quote Size: $Q_{\text{defensive}} = \max\left(1, \text{int}\left(\frac{Q_{\text{base}}}{1.0 + \tau_{\text{toxicity}}}\right)\right)$.
4. **Audit Report Generation**: Output structured `LatencyArbitrageDefenseReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Lead-Lag Relationships**: Quoting passively on secondary venues without tracking price/volume micro-bursts on lead venues.
- **Fixed Spread Quoting in High Volatility**: Maintaining static 1-cent bid-ask spreads during market-moving news events, causing heavy adverse selection losses.
- **Inadequate Cancel Latency**: Relying on software TCP stacks instead of kernel bypass (Solarflare/Onload/FPGA) for quote cancellation messages.

## Verification

- Instantiate `CrossVenueLatencyArbitrageDefense`. Input primary venue book with extreme imbalance ($V_{\text{bid}} = 1000$, $V_{\text{ask}} = 50$, $P_{\text{bid}} = 4000.00$, $P_{\text{ask}} = 4000.25$, $P_{\text{micro}} = 4000.238$). Verify toxicity index exceeds threshold ($\tau = 0.95$). Simulate cross-venue latency deficit ($\Delta t_{\text{margin}} = -120\,\mu\text{s}$). Verify manager triggers quote cancellation, widens spreads, and scales down size.
- Run `python scripts/test_cross_venue_latency_arbitrage_defensive_design.py`.

## Related Skills

- `adverse-selection-measurement-for-passive-orders`
- `latency-arbitrage-defensive-order-sizing`
---
