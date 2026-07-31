---
name: model-training-data-freshness-sla
description: >-
  Training data freshness SLA monitoring engine, tracking data pipeline ingestion lag, detecting ETL delays, and enforcing automated retraining halt governance.
domain: Data Management Global
subdomain: Feature Store Engineering & Data Pipeline Governance
tags: ["data-freshness", "sla-monitoring", "data-pipeline", "feature-store", "etl-lag", "retraining-governance", "data-contracts"]
brokers_frameworks: ["Feature Store SLAs", "Data Pipeline Contracts", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing machine learning feature stores and automated retraining pipelines for algorithmic trading strategies. Financial ML models require fresh training datasets (e.g., daily bar updates, 1-hour order book snapshots). If upstream ETL data pipelines stall or vendor market data feeds experience ingestion lag, retraining models on outdated datasets causes **Training Data Staleness**, producing alpha signal degradation. This module tracks data ingestion lag ($\Delta t_{\text{lag\_hours}}$), audits SLA threshold compliance ($24\text{h}, 36\text{h}, 48\text{h}$ ceilings), and triggers automated retraining halts.

## Prerequisites

- Freshness SLA configuration (`model_id`, `dataset_name`, `target_sla_hours`: e.g. 24.0, `warning_sla_hours`: e.g. 36.0, `breach_sla_hours`: e.g. 48.0, `action_on_breach`: `'HALT_MODEL_RETRAINING'`, `'REDUCE_CONFIDENCE'`).
- Ingested dataset metadata (`latest_record_timestamp_epoch`, `current_system_timestamp_epoch`, `total_record_count`, `missing_days_count`).

## Workflow

1. **Ingestion Lag Calculation**:
   - Compute data lag in hours:
     $$\Delta t_{\text{lag\_hours}} = \frac{T_{\text{current}} - T_{\text{latest\_record}}}{3600.0}$$
2. **SLA Compliance Audit**:
   - If $\Delta t_{\text{lag\_hours}} \le T_{\text{target\_sla\_hours}} \implies$ Status `SLA_COMPLIANT`.
   - If $T_{\text{target}} < \Delta t_{\text{lag\_hours}} \le T_{\text{warning}} \implies$ Status `SLA_WARNING_NEAR_LIMIT`.
   - If $\Delta t_{\text{lag\_hours}} > T_{\text{breach}} \implies$ Status `SLA_BREACH_CRITICAL`.
3. **Automated Governance Action Execution**:
   - If critical SLA breach occurs $\implies$ Trigger `HALT_MODEL_RETRAINING` and block deployment of stale model weights.
4. **Audit Report Generation**: Output structured `FreshnessSlaReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silent Retraining on Stale Data**: Retraining ML models on stale feature store snapshots without freshness SLA checks, overwriting good weights with outdated ones.
- **Using System Ingestion Time Instead of Event Time**: Measuring freshness by when records reached local databases rather than actual market event timestamps.
- **Ignoring Missing Days/Gaps**: Treating data with zero lag as fresh even when intermediate market days are missing from the series.

## Verification

- Instantiate `TrainingFreshnessSlaEngine`. Audit fresh feature store dataset ($\text{lag} = 5.0\text{ hours} \le 24.0\text{h}$ target) $\implies$ verify `SLA_COMPLIANT` and action `PROCEED_NORMAL`. Audit stalled ETL pipeline ($\text{lag} = 52.0\text{ hours} > 48.0\text{h}$ breach limit) $\implies$ verify `SLA_BREACH_CRITICAL` and action `HALT_MODEL_RETRAINING`.
- Run `python scripts/test_training_freshness_sla.py`.

## Related Skills

- `model-staleness-detection`
- `data-quality-monitoring-dashboard`
---
