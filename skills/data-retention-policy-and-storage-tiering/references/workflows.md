# Workflows for Data Retention Policy and Storage Tiering

1. **Age & Usage Evaluation**:
   - Classify dataset by age in days and query frequency.
2. **Target Tier Recommendation**:
   - Assign `HOT_NVME`, `WARM_PARQUET_S3`, `COLD_GLACIER`, or `DEEP_ARCHIVE`.
3. **Cost Savings Calculation**:
   - $\text{Savings} = (\text{Price}_{\text{current}} - \text{Price}_{\text{recommended}}) \times \text{Size}_{\text{GB}}$.
4. **Lifecycle Execution**:
   - Apply S3 Lifecycle rules and execute Parquet compaction.
