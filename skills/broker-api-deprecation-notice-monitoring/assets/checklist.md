# Pre-Flight Checklist: Deprecation Monitoring

Use this checklist before merging your broker adapter changes into production:

- [ ] **HTTP Client Middleware integration**: The `BrokerDeprecationMonitor.inspect_http_headers` method is hooked into the HTTP client's global response handler.
- [ ] **Thread Safety**: The integration uses `BrokerDeprecationMonitor` correctly across threads/async workers without raising race conditions.
- [ ] **Callback Configuration**: The `alert_callback` is correctly wired to the firm's alerting router (Slack/PagerDuty/Email).
- [ ] **UTC Datetimes**: System clock relies on UTC, and the monitor accurately computes time-deltas agnostic to local server host timezone.
- [ ] **Link Extraction Tested**: Monitor can extract `rel="sunset"` link URLs pointing to the broker's migration docs.
- [ ] **Changelog Cron Job**: An external cron or Celery worker is scheduled to invoke `parse_changelog_entry` at least once every 24 hours per broker.
- [ ] **Error Muting**: Malformed headers from brokers do not crash the bot's critical path; monitor fails silently and logs the parsing error instead.
