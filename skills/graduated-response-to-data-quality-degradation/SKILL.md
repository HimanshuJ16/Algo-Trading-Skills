---
name: graduated-response-to-data-quality-degradation
description: >-
  Real-time risk management engine for monitoring market data quality degradation (stale ticks, sequence gaps, price spikes, crossed books) and executing graduated de-risking tiers.
domain: Real-Time Architecture & Risk
subdomain: Data Quality Monitoring & De-Risking
tags: ["data-quality", "de-risking", "graduated-response", "stale-ticks", "sequence-gaps", "price-spikes", "circuit-breaker"]
brokers_frameworks: ["Level 2 Order Book", "Kafka / FIX Sequence Tracking", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in algorithmic trading execution systems, feed handler monitoring, and real-time risk circuit breakers. Trading on corrupted or delayed market data ("garbage in, garbage out") leads to severe execution losses. Rather than relying on a binary on/off switch, this module implements a **graduated de-risking response**: Tier 0 (Normal: 100% size), Tier 1 (Minor Degradation: 50% size haircut), Tier 2 (Moderate Degradation: Block new entries, allow exits), Tier 3 (Severe Outage: Emergency halt, mass cancel, flatten positions).

## Prerequisites

- Market data metrics (`stale_time_seconds`, `missing_sequence_count`, `price_spike_anomaly_detected`, `crossed_book_detected`, `bid_ask_spread_multiplier`).
- Configured quality score penalties and tier thresholds.

## Workflow

1. **Data Quality Metric Ingestion**:
   - Audit stale time ($T_{\text{stale}}$), FIX sequence number gaps, price spike anomalies, and crossed books ($\text{Bid} > \text{Ask}$).
2. **Quality Score Calculation ($0 - 100\%$)**:
   - $Q = 100 - \text{Penalties}$.
   - Stale penalty: $-10\%$ per second $> 1.0\text{s}$.
   - Missing sequence penalty: $-2\%$ per missing message.
   - Price spike penalty: $-25\%$.
   - Crossed book penalty: $-50\%$.
3. **Graduated De-Risking Tier Classification**:
   - $Q \ge 90\% \implies$ **Tier 0**: `ALLOW_FULL_TRADING` (100% size).
   - $70\% \le Q < 90\% \implies$ **Tier 1**: `REDUCE_SIZE_50_PCT` (50% haircut).
   - $40\% \le Q < 70\% \implies$ **Tier 2**: `BLOCK_NEW_ENTRIES` (Exits only).
   - $Q < 40\% \implies$ **Tier 3**: `EMERGENCY_HALT_AND_FLATTEN` (Mass cancel & flatten).
4. **Audit Report Generation**: Output structured `DataQualityDeRiskReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Binary On/Off Shutters**: Immediately hard-killing bots on transient 1-tick delays, causing frequent unnecessary strategy restarts.
- **Trading Through Crossed Books**: Executing orders when bid $>$ ask due to feed handler parsing errors.
- **Ignoring Sequence Number Gaps**: Processing out-of-order ticks without auditing missing packet counts.

## Verification

- Instantiate `DataQualityDeRiskerEngine`. Test Clean Feed ($Q=100\%$) $\implies$ verify Tier 0 `ALLOW_FULL_TRADING`. Test Minor Delay ($T_{\text{stale}}=2.0\text{s}, Q=80\%$) $\implies$ verify Tier 1 `REDUCE_SIZE_50_PCT`. Test Crossed Book ($Q=30\%$) $\implies$ verify Tier 3 `EMERGENCY_HALT_AND_FLATTEN`.
- Run `python scripts/test_data_quality_de-risker.py`.

## Related Skills

- `data-quality-monitoring-dashboard`
- `feed-handler-canary-deployment`
---
