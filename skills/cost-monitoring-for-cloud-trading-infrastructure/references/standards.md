# Standards for Cloud Cost Monitoring

| Metric | Engineering Standard |
|---|---|
| Z-Score Threshold | Cost spikes exceeding $Z \ge 3.0$ MUST trigger immediate FinOps alerts. |
| Tagging Compliance | 100% of cloud resources MUST be tagged with `Environment`, `Service`, and `StrategyID`. |
| Unit Cost Primacy | Spend evaluation MUST track unit economics (Cost / 10k Trades) alongside raw dollar totals. |