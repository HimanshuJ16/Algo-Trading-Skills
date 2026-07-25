# Deep Workflow Reference — broker-status-page-monitoring-integration

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Status Feed JSON Ingestion**:
   - Issue GET to Statuspage.io endpoint (`https://status.{broker}.com/api/v2/summary.json`).
   - Extract `status.indicator` (`none`, `minor`, `major`, `critical`) and `components` list.

2. **Classify Platform Health State**:
   - Map indicator to `OPERATIONAL`, `DEGRADED`, or `MAJOR_OUTAGE`.

3. **Diagnose Execution Failures**:
   - On exception, evaluate platform state.
   - If `MAJOR_OUTAGE`, classify as `EXTERNAL_BROKER_OUTAGE` and suppress code bug escalations.
   - If `OPERATIONAL`, classify as `INTERNAL_APPLICATION_BUG`.

## Production Implementation Reference

- Reference code: `scripts/status_monitor.py` (`BrokerStatusPageMonitor`, `BrokerStatusSummary`, `FailureDiagnosisResult`).
- Automated unit tests: `scripts/test_status_monitor.py`.
