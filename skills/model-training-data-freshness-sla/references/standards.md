# Standards for Training Data Freshness SLA

| Metric | Engineering Standard |
|---|---|
| SLA Metric Standard | Freshness MUST be measured using event timestamps, NOT ingestion timestamps. |
| Critical Breach Governance | Model retraining MUST be halted if data lag exceeds maximum SLA limit. |
| Audit Frequency | Data freshness SLAs MUST be evaluated prior to every model retraining job. |
