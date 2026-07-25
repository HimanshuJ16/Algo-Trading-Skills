# Checklist for Automated Rollback Systems

- [ ] Confirm the engine tracks both technical (latency) AND business/trading (order rejects) metrics.
- [ ] Confirm thresholds are calibrated accurately to avoid false positive rollbacks during normal market open volatility.
- [ ] Ensure the CI/CD pipeline correctly handles the `should_rollback` boolean.
- [ ] Run test suite: `python scripts/test_anomaly_rollback_trigger.py`.

## Sign-off
- DevOps Engineer: ___________________________
- Date: ___________________________