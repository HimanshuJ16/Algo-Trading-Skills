# Deep Workflow Reference — broker-api-deprecation-notice-monitoring

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **HTTP Response Header Scanning (RFC 8594)**:
   - On every REST API response, inspect headers for `Sunset`, `Deprecation`, and `X-API-Deprecation-Warning`.
   - Parse date string into UTC date.

2. **Developer Changelog RSS Ingestion**:
   - Periodically poll broker changelog feeds.
   - Scan titles and bodies for keywords (`deprecated`, `sunset`, `breaking change`, `end of life`).

3. **Sunset Countdown & Urgency Classification**:
   - Compute $D = \text{SunsetDate} - \text{CurrentDate}$.
   - Classify urgency: `CRITICAL_SUNSET_IMMINENT` ($D \le 7$), `WARNING_30_DAYS` ($7 < D \le 30$), `NOTICE` ($D > 30$).

4. **Alert Escalation**:
   - Trigger ops alerts for critical deprecation notices before API shutdown.

## Production Implementation Reference

- Reference code: `scripts/deprecation_monitor.py` (`BrokerDeprecationMonitor`, `DeprecationNotice`, `DeprecationUrgency`).
- Automated unit tests: `scripts/test_deprecation_monitor.py`.
