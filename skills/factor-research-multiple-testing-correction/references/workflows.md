# Workflows for Factor Research Multiple Testing Correction

1. **Factor Batch Ingestion**:
   - Ingest candidate factor test statistics (t-stats, p-values, sample sizes).
2. **P-Value Sorting & Rank Calculation**:
   - Rank factor p-values in ascending order $p_{(1)} \le \dots \le p_{(M)}$.
3. **Multi-Method Correction**:
   - Compute Bonferroni, Holm-Bonferroni, Benjamini-Hochberg FDR, and HLZ t>=3.0 thresholds.
4. **Alpha Factor Promotion Filtering**:
   - Approve only factors passing FDR/HLZ benchmarks for production backtesting.