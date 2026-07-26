# Standards for Drift vs. Staleness Classification

| Metric | Engineering Standard |
|---|---|
| Staleness Check Priority | Data staleness MUST be checked prior to distribution drift testing to avoid diagnosing stale data as model drift. |
| PSI Threshold | Feature PSI $> 0.25$ indicates significant population distribution shift. |
| Remediation Isolation | Model retraining MUST NOT be executed if the root cause is classified as `DATA_STALENESS`. |