---
name: data-quality-monitoring-dashboard
description: Real-time market data quality monitoring dashboard engine for auditing
  completeness, timeliness (latency), accuracy (outliers), uniqueness, and feed liveness
  across data vendors.
domain: Data Management Global
subdomain: Data Quality & Observability
tags:
- data-quality
- observability
- data-downtime
- completeness-score
- timeliness-latency
- outlier-detection
- data-monitoring
brokers_frameworks:
- Prometheus
- Grafana
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in production trading operations, market data ingestion servers, and feature stores to monitor data quality metrics across vendors (Bloomberg, Refinitiv, Databento, Polygon). Poor market data quality (null prices, latency spikes, duplicate ticks, or stalled feeds) causes execution algorithms to misfire or generate bad orders. This module computes a composite Data Quality Score ($0.0$ to $100.0$), emits alerts, and recommends fallback to a secondary feed when the score drops below the configured failover threshold.

## When NOT to Use

- **As a compliance metric.** The composite score is an internal engineering signal. No regulator or standards body defines a market data DQ score — see `references/standards.md`.
- **As the outlier detector itself.** This engine consumes an `outlier_records_count` produced upstream; it does not classify outliers. Feeding it a naive z-score count will flag genuine news-driven moves as data defects.
- **As the sole liveness check.** $	ext{TPS} > 0$ does not prove freshness: a feed replaying stale data still ticks. Pair with a source-side timestamp age check.
- **For per-tick gating.** This scores batch telemetry over a window. Use a schema/bounds validator on the record path instead.

## Prerequisites

- Feed batch telemetry per `(vendor_id, symbol)` window: `total_records`, `null_records_count`, `duplicate_records_count`, `outlier_records_count`, `avg_latency_ms`, `ticks_per_second`.
- Configured thresholds: `min_acceptable_dq_score` (default `min_healthy_score=85.0`) and `critical_failover_score` (default `70.0`), which must be strictly lower.

## Workflow

1. **Pillar Metric Evaluation** — each score is floored at $0.0$ and rounded to 2dp before weighting. Penalty factors and the latency scale are constructor parameters; the defaults below are engineering choices, not published standards.
   - **Completeness (25%)**: $S_{	ext{comp}} = 100 - 	ext{NullPct} 	imes f_{	ext{null}}$, default $f_{	ext{null}} = 2.0$.
   - **Timeliness (25%)**: $S_{	ext{time}} = 100 	imes \left(1 - rac{	ext{LatencyMs}}{L_0}ight)$, default $L_0 = 500	ext{ ms}$.
   - **Accuracy (25%)**: $S_{	ext{acc}} = 100 - 	ext{OutlierPct} 	imes f_{	ext{out}}$, default $f_{	ext{out}} = 5.0$ (steepest: one mispriced tick can make an algo cross the spread).
   - **Uniqueness (15%)**: $S_{	ext{uniq}} = 100 - 	ext{DupPct} 	imes f_{	ext{dup}}$, default $f_{	ext{dup}} = 2.0$.
   - **Liveness (10%)**: $S_{	ext{live}} = 100$ if $	ext{TPS} > 0$ else $0$.
2. **Composite DQ Scoring**:
   - $	ext{DQ Score} = 0.25 S_{	ext{comp}} + 0.25 S_{	ext{time}} + 0.25 S_{	ext{acc}} + 0.15 S_{	ext{uniq}} + 0.10 S_{	ext{live}}$.
3. **Alerting & Failover Directives** — first matching branch wins, and all comparisons are strict `<`, so a score exactly equal to a threshold falls in the healthier band:
   - $	ext{TPS} = 0 \implies$ `CRITICAL` + failover, **regardless of the composite score**. A stalled feed's last records are clean, so the count-based pillars stay high; liveness must override, not merely contribute its 10%.
   - $	ext{DQ Score} < 70.0 \implies$ `CRITICAL` + failover.
   - $	ext{DQ Score} < 85.0 \implies$ `WARNING`, no failover.
4. **Degenerate & Corrupt Batches**:
   - `total_records == 0` returns a `CRITICAL` dead-feed report with score $0.0$ and an empty `dimensions` list — check for that empty list before indexing it.
   - Structurally impossible telemetry (negative counts, a defect count above `total_records`, non-finite or negative latency/TPS) raises `ValueError`. Catch and alert on it; a corrupt collector must not be reported as a clean feed.
5. **Audit Report Generation**: Output structured `DataQualityMonitoringReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a ticking feed as a live feed**: $	ext{TPS} > 0$ only proves messages are arriving, not that they are current. A vendor replaying a stale snapshot, or a gateway echoing its last book, keeps the liveness pillar at $100$ while the prices are minutes old. Corroborate with the age of the source-side exchange timestamp.
- **Letting liveness be outvoted by its weight**: liveness is only 10% of the composite, so a stalled feed with otherwise-clean records scores $pprox 89.95$ — above the healthy floor. The $	ext{TPS} = 0$ override exists for exactly this reason; do not "simplify" it into a plain weighted term.
- **Averaging latency over long windows**: a 1-hour mean hides a 2-second ingestion stall at the open. Score short windows, and track a high percentile alongside the mean.
- **Conflating outliers with volatility**: flagging real price movement during news events as a data defect, without cross-vendor verification, causes failover *away* from a correct feed precisely when the market is moving.
- **Swallowing the `ValueError`**: wrapping `audit_feed_quality` in a bare `except` and continuing turns a broken telemetry collector into silence, which reads as "no problems found".
- **Misconfiguring the bands**: setting `critical_failover_score >= min_healthy_score` would make `WARNING` unreachable and escalate every degraded feed straight to failover. The constructor now rejects this.

## Verification

- Instantiate `DataQualityMonitoringEngine()` with defaults. Audit a clean Bloomberg batch (1000 records, 0 nulls/duplicates/outliers, $2	ext{ ms}$ latency, 50 TPS): timeliness is $99.6$, so the composite is **$99.9$**, status `HEALTHY`, `is_failover_recommended` `False`.
- Audit a degraded live Refinitiv batch (10% nulls, 5% outliers, 10% duplicates, $400	ext{ ms}$ latency, 25 TPS): composite **$65.75$**, status `CRITICAL`, `is_failover_recommended` `True`, with a `CRITICAL DATA QUALITY BREACH` alert (not `DEAD FEED`).
- Audit a stalled but otherwise clean feed ($1	ext{ ms}$ latency, 0 TPS): composite **$89.95$** — above `min_healthy_score` — yet status must still be `CRITICAL` with failover recommended and a `DEAD FEED` alert.
- Confirm `audit_feed_quality` raises `ValueError` for `avg_latency_ms=-50.0` and for `null_records_count=-1000`, both of which otherwise yield composites above $100$.
- Run `python -m unittest discover -s skills/data-quality-monitoring-dashboard/scripts`.

## Related Skills

- `data-pipeline-schema-contract-testing`
- `vendor-outage-fallback-data-source-hierarchy`
- `graduated-response-to-data-quality-degradation`
- `multi-source-price-reconciliation-tie-breaking`
