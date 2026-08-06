---
name: strategy-specific-data-dependency-mapping
description: >-
  Production-grade Strategy-Specific Data Dependency Mapping Engine tracking data lineage DAGs, freshness SLA cutoffs, vendor outage fallback hierarchies, and trading readiness scores.
domain: Data Management & Infrastructure Governance
subdomain: Lineage & SLA Data Governance
tags: ["data-dependency", "lineage-dag", "sla-freshness", "fallback-hierarchy", "vendor-failover", "trading-readiness"]
brokers_frameworks: ["OpenLineage DAG Framework", "Data Freshness SLAs", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing data dependencies and operational readiness across institutional quantitative strategies. Each strategy relies on specific data feeds (e.g. L2 Orderbook, Social Sentiment, SEC filings, FX spot rates). A failure or latency lag in a critical data dependency can cause trading algorithms to execute on stale or corrupt prices. This engine maps strategy data dependencies, audits data freshness against SLA cutoffs ($\le 300\text{s}$), triggers vendor fallback hierarchies (`PRIMARY_VENDOR` $\to$ `SECONDARY_VENDOR` $\to$ `DEGRADED`), and computes a Strategy Readiness Score (0-100%).

## Prerequisites

- Defined data dependency nodes (`DataDependencyNode`: `feed_id`, `criticality`, `primary_vendor`, `secondary_vendor`, `max_acceptable_lag_seconds`, `is_schema_valid`).
- Real-time feed status updates (`FeedStatusUpdate`: `feed_id`, `last_updated_epoch`, `current_vendor`, `is_healthy`, `schema_error`).

## Workflow

1. **Dependency Lineage Mapping**:
   - Register data dependencies with assigned criticality (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`) and vendors.
2. **Freshness SLA & Schema Contract Audit**:
   - Audit time lag ($\text{now} - t_{\text{updated}}$) against max acceptable SLA cutoff and check schema validity.
3. **Fallback Hierarchy Execution**:
   - If primary vendor fails, pivot to secondary vendor (`FALLBACK_ACTIVE`) with minor readiness weight penalty.
   - If critical dependency fails both primary and secondary vendors, block strategy execution (`is_ready = False`).
4. **Readiness Score Calculation**:
   - Calculate weighted readiness score (0-100%) and output structured `StrategyDataDependencyReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading on Stale Data**: Executing algorithm orders when a primary data feed has silently hung or lagged beyond its SLA cutoff.
- **Single Vendor Point of Failure**: Lacking a configured secondary vendor fallback for critical orderbook or reference price feeds.
- **Unvalidated Schema Changes**: Failing to check data contract schemas, allowing missing fields or broken data types to reach signal calculation logic.

## Verification

- Instantiate `StrategyDataDependencyEngine`. Evaluate healthy feeds $\implies$ verify `readiness_score_pct = 100.0%` and `is_strategy_ready_to_trade = True`. Pass stale primary Refinitiv feed $\implies$ verify automatic pivot to secondary Bloomberg vendor. Pass failed critical feed with no secondary vendor $\implies$ verify `is_strategy_ready_to_trade = False` and blocked dependency logged.
- Run `python scripts/test_strategy_specific_data_dependency_mapping.py`.

## Related Skills

- `vendor-outage-fallback-data-source-hierarchy`
- `reference-data-golden-source-designation`
---
