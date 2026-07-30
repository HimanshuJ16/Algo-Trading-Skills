---
name: custody-solution-uptime-and-liveness-guarantees
description: >-
  Quantitative custody liveness monitoring engine for tracking API uptime SLAs (99.9%+), MPC threshold quorum (k-of-n), and signing latency SLAs, triggering failover upon availability breaches.
domain: Crypto Custody & Security
subdomain: Custody SLA & Liveness
tags: ["custody-sla", "liveness-guarantees", "mpc-quorum", "signing-latency", "fireblocks", "bitgo", "anchorage", "uptime-monitoring"]
brokers_frameworks: ["SOC 2 Type II", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in institutional crypto trading desks and automated custody systems to monitor API uptime, MPC node liveness ($k$-of-$n$), and transaction signing latency SLAs across custody providers (Fireblocks, BitGo, Coinbase Custody, Anchorage). In 24/7/365 crypto markets, a custody outage or MPC quorum failure during market volatility prevents margin top-ups or liquidation defense. This module computes rolling uptime percentages, tracks P99 signing latency, and triggers secondary custody failover when SLAs are breached ($< 99.9\%$ uptime or signing latency $> 5000\text{ ms}$).

## Prerequisites

- Custody provider SLA configuration: `target_uptime_pct` (e.g. 99.9%), `max_signing_latency_ms` (e.g. 2000 ms), `mpc_threshold_k`, `mpc_total_n`.
- Continuous health probe & signing telemetry feeds.

## Workflow

1. **Telemetry Ingestion**:
   - Collect API ping health, MPC node status, and signing latency measurements.
2. **Uptime & Quorum Audit**:
   - $\text{Uptime Pct} = \frac{N_{\text{healthy}}}{N_{\text{total}}} \times 100\%$.
   - Check MPC threshold: If $\text{Active Nodes} < k \implies$ Trigger `MPC_QUORUM_LOST` emergency alert.
3. **P99 Signing Latency Audit**:
   - Compute P99 signing time over rolling 100 transactions.
   - If $\text{P99} > \text{SLA Max} \implies$ Flag `LATENCY_SLA_BREACH`.
4. **Secondary Signer Failover**:
   - If $\text{Uptime} < 99.9\%$ or $\text{MPC Quorum Lost} \implies$ Trigger automated failover to secondary custody provider.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring MPC Quorum Redundancy**: Relying on 2-of-3 MPC nodes without monitoring node health, suffering liveness halts when 1 node goes down for maintenance.
- **Relying on Average Signing Latency**: Tracking average signing time (500ms) while missing 15-second P99 signing spikes during high gas spikes on Ethereum.
- **Un-tested Custody Failover**: Defining a secondary custody provider without regularly testing automated failover routing under simulated outages.

## Verification

- Instantiate `CustodyLivenessMonitorEngine`. Register `Primary_Custodial_MPC` ($k=2, n=3$, target uptime $99.9\%$, max signing $2000\text{ ms}$). Simulate 100 health probes with 99.95% uptime and P99 latency $450\text{ ms}$. Verify status is `HEALTHY`. Simulate 2 MPC nodes going offline (Active $= 1 < k=2$). Verify monitor triggers `MPC_QUORUM_LOST` and directs failover to secondary provider.
- Run `python scripts/test_custody_solution_uptime_and_liveness_guarantees.py`.

## Related Skills

- `custodial-vs-non-custodial-tradeoff-assessment`
- `multi-party-computation-mpc-custody-solutions`
---
