# Standards for Model Versioning & Rollback

| Metric | Engineering Standard |
|---|---|
| Artifact Fingerprinting | All registered models MUST specify an immutable SHA-256 hash. |
| Rollback Time Objective | Automated rollbacks MUST execute within $< 100\text{ ms}$ upon circuit breaker trigger. |
| Version Retention | At least ONE previously verified `PRODUCTION` version MUST remain available in registry. |
