---
name: data-retention-policy-and-storage-tiering
description: Quantitative data storage tiering and lifecycle engine for transitioning
  market data (L2/L3 ticks, Parquet backtests, trade logs) across HOT (NVMe), WARM
  (S3), COLD (Glacier), and DEEP ARCHIVE tiers.
domain: Data Management Global
subdomain: Storage Optimization & Retention
tags:
- data-retention
- storage-tiering
- hot-warm-cold
- s3-glacier
- parquet-compaction
- cost-optimization
- sec-17a-4
brokers_frameworks:
- AWS S3 Lifecycle
- Glacier Deep Archive
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in market data infrastructure and quantitative data lakes to manage multi-tier storage lifecycles. High-frequency L2/L3 market data accumulates petabytes per year. Retaining historical tick data indefinitely in expensive HOT NVMe storage (\$0.20/GB/mo) creates astronomical cloud bills. This module evaluates dataset age and regulatory requirements (SEC Rule 17a-4 6-year retention), automatically transitioning datasets to WARM Parquet (\$0.023/GB/mo), COLD Glacier (\$0.004/GB/mo), or DEEP ARCHIVE (\$0.00099/GB/mo), quantifying monthly USD savings.

## Prerequisites

- Dataset metadata (`dataset_id`, `age_days`, `size_gb`, `current_tier`, `regulatory_retention_years`).
- Storage tier pricing model (`HOT`: \$0.20/GB, `WARM`: \$0.023/GB, `COLD`: \$0.004/GB, `DEEP_ARCHIVE`: \$0.00099/GB).

## Workflow

1. **Dataset Lifecycle Evaluation**:
   - $\le 30$ days $\implies$ `HOT_NVME`.
   - $31$ to $365$ days $\implies$ `WARM_PARQUET_S3`.
   - $366$ to $2555$ days (1-7 years) $\implies$ `COLD_GLACIER_INSTANT`.
   - $> 2555$ days ($> 7$ years): If regulatory retention required $\implies$ `DEEP_ARCHIVE`; else $\implies$ `PURGE`.
2. **Storage Cost Calculation**:
   - $\text{Current Cost} = \text{Size}_{\text{GB}} \times \text{Price}_{\text{current}}$.
   - $\text{Target Cost} = \text{Size}_{\text{GB}} \times \text{Price}_{\text{recommended}}$.
   - $\text{Monthly Savings} = \text{Current Cost} - \text{Target Cost}$.
3. **Transition Action Dispatch**: Output S3 lifecycle policies and Parquet compaction commands.
4. **Audit Report Generation**: Output structured `DataRetentionAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Keeping Multi-Year Ticks in HOT NVMe**: Leaving 3-year-old tick data in high-cost NVMe databases, inflating cloud bills by $10\times$.
- **Premature Purging of Trade Logs**: Deleting 5-year-old trade logs, violating SEC Rule 17a-4 6-year WORM retention requirements.
- **Un-compacted Small Parquet Files**: Archiving millions of $100\text{KB}$ Parquet files to Glacier, triggering massive per-object transition fee penalties.

## Verification

- Instantiate `DataRetentionPolicyEngine`. Input 100TB dataset (Age = 500 days, stored in `HOT_NVME`). Verify engine recommends transitioning to `WARM_PARQUET_S3` and calculates monthly cost reduction from \$20,000 to \$2,300 (\$17,700/mo savings). Input 50TB dataset (Age = 3,000 days, 6-year regulatory requirement expired). Verify engine recommends `PURGE` or `DEEP_ARCHIVE`.
- Run `python scripts/test_data_retention_policy_and_storage_tiering.py`.

## Related Skills

- `historical-tick-data-storage-and-compaction`
- `data-localization-requirements-for-trade-records`
---
