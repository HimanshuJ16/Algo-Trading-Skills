# Standards: Infrastructure Cost Budgeting

| Metric | Target | Rationale |
|--------|--------|-----------|
| **Compute Profiling** | Profile 1% of total grid | Ensures per-unit estimates for CPU and RAM are accurate before scaling. |
| **Spot Instance Usage** | Strongly Recommended | Can reduce compute costs by 70-90% for stateless backtest jobs. |
| **Storage Lifecycle** | Ephemeral or S3 | Don't leave large backtest logs on expensive EBS volumes; pipe them to cheaper object storage and delete quickly. |
