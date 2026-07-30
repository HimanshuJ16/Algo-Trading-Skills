---
name: data-lineage-tracking-for-audit-and-debugging
description: >-
  Quantitative data lineage tracking engine for auditing market data pipelines, feature store lineage, and model decision graphs to perform root cause debugging and impact analysis.
domain: Data Management Global
subdomain: Data Lineage & Auditability
tags: ["data-lineage", "dag-lineage", "auditability", "feature-store-lineage", "root-cause-analysis", "impact-analysis", "openlineage"]
brokers_frameworks: ["OpenLineage Standard", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative research, feature store engineering, and live trading systems to maintain end-to-end data lineage DAGs. When a live trading model generates an anomalous order signal or a backtest exhibits unexplained performance jumps, data lineage allows engineers to perform **Upstream Root Cause Analysis** (tracing a trade signal $S_t$ back to raw vendor ticks and transformation parameters) or **Downstream Impact Analysis** (identifying all downstream models impacted by a corrupted market data payload).

## Prerequisites

- Node classification schema: `DATA_SOURCE`, `TRANSFORMATION`, `FEATURE_STORE`, `MODEL_INFERENCE`, `ORDER_DECISION`.
- Node metadata: `data_hash_sha256`, `pipeline_version`, `timestamp_utc`, `schema_contract_version`.

## Workflow

1. **DAG Node & Edge Registration**:
   - Register data artifacts and transformations with SHA-256 data fingerprinting.
   - Establish parent-child dependency edges ($A \to B$).
2. **Upstream Root Cause Traversal**:
   - Given a target node (e.g. `ORDER_DECISION_99`), recursively traverse parent edges to isolate root raw data sources.
3. **Downstream Impact Traversal**:
   - Given a corrupt data source (e.g. `BLOOMBERG_TICK_RAW`), recursively traverse child edges to flag all affected downstream features and active trading models.
4. **Audit Report Generation**: Output structured `DataLineageAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-tracked Schema Drift**: Modifying feature transformation logic without updating lineage graph pipeline versions, making historical backtest reproduction impossible.
- **Dangling Nodes**: Registering model inferences without linking them back to the specific feature store snapshot version used during inference.
- **Ignoring Data Fingerprinting**: Tracking dataset names without computing SHA-256 content hashes, failing to detect silent data mutation or backfills.

## Verification

- Instantiate `DataLineageTrackerEngine`. Build a DAG: Raw Tick Feed (`SRC_1`) $\to$ VWAP Transformation (`TR_1`) $\to$ Momentum Feature (`FEAT_1`) $\to$ Signal Engine (`MODEL_1`). Trigger **Upstream Traversal** on `MODEL_1` and verify it traces back to `SRC_1`. Trigger **Downstream Traversal** on `SRC_1` and verify it identifies `MODEL_1` as an impacted node.
- Run `python scripts/test_data_lineage_tracking.py`.

## Related Skills

- `data-pipeline-schema-contract-testing`
- `historical-tick-data-storage-and-compaction`
---
