# Workflows for Configuration Audit Logging

1. **Intercept**: Any UI dashboard, CLI tool, or API endpoint that mutates a configuration parameter must route the request through the `ConfigurationAuditLogger`.
2. **Data Capture**: The frontend must capture the user's ID (via SSO/JWT) and explicitly prompt them for a text `justification`.
3. **Execution**: If `process_change_request()` returns `is_approved = True`, the system commits the change to the database.
4. **Log Forwarding**: The `AUDIT_LOG_ENTRY` emitted by the python `logger` is scraped by a daemon (e.g., Fluentd, Filebeat) and forwarded to a Write-Once-Read-Many (WORM) compliant centralized logging server.