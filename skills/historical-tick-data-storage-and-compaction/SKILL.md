---
name: historical-tick-data-storage-and-compaction
description: >-
  Quantitative storage engine for compressing multi-million historical tick datasets using Delta Encoding, Parquet columnar partitioning, and Zstandard compression across Hot/Warm/Cold storage tiers.
domain: Data Management Global
subdomain: Historical Tick Storage & Compaction Architecture
tags: ["tick-storage", "parquet", "delta-encoding", "zstandard", "columnar-compression", "storage-tiering", "data-compaction"]
brokers_frameworks: ["Apache Parquet", "PyArrow / DuckDB", "Snappy / Zstd", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing high-frequency tick databases, backtest data repositories, and cloud storage compaction pipelines. High-frequency tick data (millions of raw trade/quote ticks per day per symbol) consumes terabytes of disk space if stored as uncompressed JSON or CSV. This module applies **Delta Encoding** ($\Delta t_i = t_i - t_{i-1}$, $\Delta p_i = P_i - P_{i-1}$) and columnar **Parquet Zstandard compression**, achieving $5\times - 20\times$ compression ratios across Hot/Warm/Cold storage tiers.

## Prerequisites

- Raw tick records (`timestamp_ns`, `symbol`, `price`, `quantity`, `side`).
- Storage tier specifications (`HOT_TIER`, `WARM_TIER`, `COLD_TIER`).
- Target compression ratio threshold ($\ge 5.0\times$).

## Workflow

1. **Raw Tick Data Ingestion**:
   - Ingest uncompressed tick batch (`timestamp_ns`, `price`, `quantity`, `side`).
2. **Delta Encoding Pre-Processing**:
   - Encode timestamps: $\Delta t_i = t_i - t_{i-1}$ (for $i \ge 1$).
   - Encode tick prices: $\Delta p_i = \text{round}((P_i - P_{i-1}) \times 10^4)$.
3. **Columnar Parquet & Zstandard Compaction**:
   - Compress delta-encoded binary arrays into Parquet/Zstd format.
   - Calculate Compression Ratio:
     $$\text{Compression Ratio} = \frac{\text{Raw Size Bytes}}{\text{Compacted Size Bytes}}$$
4. **Audit Report Generation**: Output structured `TickStorageCompactionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Storing Ticks in Uncompressed CSV Files**: Consuming $10\times$ more storage space and suffering slow I/O read speeds during backtest data loading.
- **Failing to Delta-Encode Timestamps**: Compressing raw 64-bit nanosecond epoch timestamps without computing deltas, forfeiting $50\%$ compression gains.
- **Un-Partitioned Storage Layout**: Storing all historical ticks in a single giant file instead of partitioning by `symbol/year=YYYY/month=MM`.

## Verification

- Instantiate `HistoricalTickStorageCompactionEngine`. Input 10,000 raw ticks (Raw Size $= 400,000$ bytes). Apply Delta Encoding & Parquet Zstd Compaction (Compacted Size $= 40,000$ bytes) $\implies$ verify engine achieves $10.0\times$ compression ratio ($90\%$ space savings) and assigns `COLD_TIER` archiving status.
- Run `python scripts/test_historical_tick_data_storage_and_compaction.py`.

## Related Skills

- `historical-tick-data-storage-and-compaction`
- `data-retention-policy-and-storage-tiering`
---
