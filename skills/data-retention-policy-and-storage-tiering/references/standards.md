# Standards for Data Retention Policy and Storage Tiering

| Metric | Engineering Standard |
|---|---|
| HOT Tier Age Limit | Datasets older than 30 days MUST be transitioned from HOT NVMe to WARM Parquet/S3. |
| COLD Tier Age Limit | Datasets older than 365 days MUST be transitioned to COLD Glacier Instant Retrieval. |
| Regulatory Retention Cap | SEC 17a-4 trade records MUST be retained for a minimum of 6 years prior to purging. |
