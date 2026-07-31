# Workflows for Historical Tick Data Storage and Compaction

1. **Tick Data Ingestion**:
   - Ingest raw uncompressed tick dataset.
2. **Delta Encoding Transformation**:
   - Compute timestamp and price deltas to reduce bit variance.
3. **Columnar Zstd Compaction**:
   - Write compressed Parquet / Zstandard binary files to disk.
4. **Storage Tiering Audit**:
   - Assign tier (Hot, Warm, Cold) and generate audit report.
