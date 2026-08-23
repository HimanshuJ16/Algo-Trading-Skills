# Standards for Cloud Cost Monitoring

| Metric | Engineering Standard |
|---|---|
| Z-Score Threshold | Cost spikes exceeding $Z \ge 3.0$ (with > 30% mean increase) MUST trigger immediate FinOps alerts. |
| Tagging Compliance | 100% of cloud resources MUST be tagged with `Environment`, `Service`, and `StrategyID`; baselines MUST be scoped to a single (service, environment) pair. |
| Unit Cost Primacy | Spend evaluation MUST track unit economics (Cost per trade — scale ×10,000 for a per-10k-trades view) alongside raw dollar totals. |