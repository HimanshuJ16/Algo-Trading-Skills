---
name: market-data-feed-arbitration-across-vendors
description: >-
  Use when consuming redundant market data streams from dual data vendors (e.g. Bloomberg and Refinitiv) to execute real-time feed arbitration, detect price divergence spikes, filter bad ticks, and fail over seamlessly when one vendor feed becomes stale.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "feed-arbitration", "dual-vendors", "bad-tick-filter", "price-divergence", "redundancy"]
brokers_frameworks: ["Feed Arbitrator Engine", "Python Real-Time Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating mission-critical quantitative strategies fed by dual redundant market data vendors (e.g., Direct Exchange Feed vs Aggregator Feed, or Bloomberg B-PIPE vs Refinitiv ELEKTRON). Bad ticks, data spikes, or vendor-specific outages can trigger catastrophic false trading signals. This skill arbitrates between parallel streams, filters price outliers exceeding tolerance thresholds ($\delta > \epsilon$), and maintains continuous clean tick feeds.

## Prerequisites

- Primary and Secondary market data feed streams for target symbols.
- Configurable price divergence tolerance threshold (e.g. 0.05% or 5 bps).

## Workflow

1. **Ingest Dual Vendor Ticks**:
   - Receive tick updates from Vendor A (Primary) and Vendor B (Secondary) with timestamping.

2. **Compute Relative Price Divergence**:
   - Calculate divergence $\delta = \frac{|P_A - P_B|}{\min(P_A, P_B)}$.

3. **Arbitrate Price Consensus & Outlier Filter**:
   - If $\delta \le \text{tolerance}$: Emit consensus midpoint/median price.
   - If $\delta > \text{tolerance}$: Detect bad tick spike, quarantine the outlier vendor feed, and emit the valid feed price.

4. **Stale Feed Timeout Detection**:
   - If one vendor feed stops updating (latency gap $> 1.0\text{s}$), flag vendor feed as `STALE` and fail over exclusively to the active vendor feed.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Overly Tight Divergence Tolerances**: Setting tolerance too low (e.g., 0.0001%), triggering false feed disagreement alarms on normal bid/ask quote spread differences.
- **Ignoring Stale Timestamp Gaps**: Treating a stale price held for 10 seconds as a valid consensus quote against a live updating feed.
- **Cascading Quarantine Lockout**: Quarantining both feeds simultaneously when a sudden real market gap occurs (e.g., earnings release spike).

## Verification

- Simulate dual feeds within 0.02% price tolerance and verify consensus median price output.
- Inject a 5.0% bad tick spike into Vendor B and verify Vendor B quarantine and clean Vendor A output.
- Run `python scripts/test_feed_arbitrator.py` and confirm 100% pass rate.

## Related Skills

- `clock-skew-correction-for-tick-timestamps`
- `multi-exchange-feed-normalization`
- `broker-status-page-monitoring-integration`
---
