# Deep Workflow Reference — model-monitoring-dashboard-for-non-technical-stakeholders

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Ingest Raw Telemetry**: Read accuracy %, staleness days, feature drift PSI, and prediction latency.
2. **Evaluate Sub-Component Statuses**: Assign GREEN / AMBER / RED for each metric against defined risk thresholds.
3. **Aggregate Traffic Light Status**: Set overall health status to worst sub-component status.
4. **Generate Recommended Action Plan**: Output plain-language recommendation (`NO_ACTION_REQUIRED`, `SCHEDULE_RETRAIN_AND_REVIEW`, `HALT_TRADING_IMMEDIATELY`).

## Production Implementation Reference

- Reference code: `scripts/monitoring_dashboard.py` (`NonTechnicalMonitoringDashboard`, `DashboardReport`, `HealthStatus`).
- Automated unit tests: `scripts/test_monitoring_dashboard.py`.
