# Standards for Centralized Observability

| Metric | Engineering Standard |
|---|---|
| Log Format | Logs MUST be formatted in structured JSON containing trace `correlation_id`. |
| Credential Redaction | Sensitive keys (`api_key`, `secret`, `private_key`) MUST be masked to `[REDACTED]`. |
| Error Spike Alert | Error rates exceeding 10 errors/window MUST trigger an observability alert. |
