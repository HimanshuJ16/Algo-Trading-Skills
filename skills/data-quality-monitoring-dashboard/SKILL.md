---
name: data-quality-monitoring-dashboard
description: >-
  Real-time market data quality monitoring dashboard engine for auditing completeness, timeliness (latency), accuracy (outliers), uniqueness, and feed liveness across data vendors.
domain: Data Management Global
subdomain: Data Quality & Observability
tags: ["data-quality", "observability", "data-downtime", "completeness-score", "timeliness-latency", "outlier-detection", "data-monitoring"]
brokers_frameworks: ["Prometheus", "Grafana", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in production trading operations, market data ingestion servers, and feature stores to monitor data quality metrics across vendors (Bloomberg, Refinitiv, Databento, Polygon). Poor market data quality (null prices, 5-second latency spikes, duplicate ticks, or dead feeds) causes execution algorithms to misfire or generate bad orders. This module computes composite Data Quality Scores ($0.0$ to $100.0$), issues real-time alerts, and triggers automated fallback to secondary feeds when DQ drops below target thresholds ($< 85.0$).

## Prerequisites

- Feed batch telemetry (`vendor_id`, `symbol`, `record_count`, `null_count`, `duplicate_count`, `outlier_count`, `avg_latency_ms`, `ticks_per_sec`).
- Quality score threshold: `min_acceptable_dq_score` (e.g. 85.0).

## Workflow

1. **Pillar Metric Evaluation**:
   - **Completeness (25%)**: $S_{\text{comp}} = 100 - 100 \times \frac{N_{\text{null}}}{N_{\text{total}}}$.
   - **Timeliness (25%)**: $S_{\text{time}} = \max\left(0, 100 - \frac{\text{LatencyMs}}{5.0}\right)$.
   - **Accuracy (25%)**: $S_{\text{acc}} = 100 - 100 \times \frac{N_{\text{outlier}}}{N_{\text{total}}}$.
   - **Uniqueness (15%)**: $S_{\text{uniq}} = 100 - 100 \times \frac{N_{\text{dup}}}{N_{\text{total}}}$.
   - **Liveness (10%)**: $S_{\text{live}} = 100$ if $\text{TPS} > 0$ else $0$.
2. **Composite DQ Scoring**:
   - $\text{DQ Score} = 0.25 S_{\text{comp}} + 0.25 S_{\text{time}} + 0.25 S_{\text{acc}} + 0.15 S_{\text{uniq}} + 0.10 S_{\text{live}}$.
3. **Alerting & Failover Directives**:
   - If $\text{DQ Score} < 70.0$ or $\text{TPS} == 0 \implies$ Flag `CRITICAL` & trigger secondary feed failover.
   - If $\text{DQ Score} < 85.0 \implies$ Flag `WARNING`.
4. **Audit Report Generation**: Output structured `DataQualityMonitoringReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-monitored Dead Feeds**: Failing to check ticks per second ($\text{TPS} = 0$), leaving algorithms trading on stale order book snapshots.
- **Ignoring Microsecond Latency Spikes**: Averaging latency over 1-hour intervals, hiding 2-second ingestion delays during market open volatility.
- **Conflating Outliers with Volatility**: Flagging real price movement during news events as data quality errors without statistical cross-vendor verification.

## Verification

- Instantiate `DataQualityMonitoringEngine`. Audit a healthy Bloomberg feed (0 nulls, 0 duplicates, 0 outliers, 2ms latency). Verify DQ Score = 100.0 (`HEALTHY`). Audit a degraded Refinitiv feed (5% nulls, 500ms latency, 0 TPS dead feed). Verify DQ Score < 60.0, status `CRITICAL`, and `is_failover_recommended` = True.
- Run `python scripts/test_data_quality_monitoring_dashboard.py`.

## Related Skills

- `data-pipeline-schema-contract-testing`
- `vendor-outage-fallback-data-source-hierarchy`
---
