# Checklist for Automated Rollback Systems

## Prerequisites
- [ ] Python 3.10+ runtime available in the CI/CD pipeline.
- [ ] Telemetry aggregator supplies per-version `DeploymentHealthMetrics` (latency, 5xx, order reject rate).
- [ ] Previous known-good version retained in warm standby (Blue/Green); rollback is a traffic reroute.
- [ ] Rollback thresholds agreed **before** the deploy, calibrated against the prior version's baseline.
- [ ] Message/cache formats and schema migrations are rollback-safe (forward/backward compatible).

## Configuration Validation
- [ ] `max_latency_ms`, `max_http_5xx_error_rate`, `max_order_reject_rate` set and within valid ranges (rates in `[0, 1]`), and every threshold is a finite number (no `NaN`/`Inf` sentinel to "disable" a metric).
- [ ] `consecutive_failures_required >= 2` (do not roll back on a single transient spike).
- [ ] `rollback_cooldown_seconds` set (default 300s) to prevent rollback loops.
- [ ] `max_rollbacks_per_deployment` set (default 1); `0` only for detection-only / manual-approval mode.
- [ ] `engine.reset()` called at the start of each new deployment.

## Deployment / Burn-in
- [ ] Burn-in window configured (5–15 min for low-latency trading; longer for batch).
- [ ] Poll interval set (5–10s); only one deployment evaluation active at a time.
- [ ] `market_open_volatility` flag set on snapshots overlapping market-open / fast-market windows.
- [ ] Metrics scraped **per version/SHA**, not per fleet.
- [ ] Poller treats a missing series, a timed-out scrape, or a sample older than the poll interval as an evaluation failure that escalates — never as a healthy sample, and never back-filled with the previous value or `0`.
- [ ] Poller handles the `ValueError` raised for a non-finite metric by escalating, not by coercing the value.
- [ ] Dashboard shows version/SHA, env, traffic share, error rate, latency, saturation, dependency health.

## Rollback Decision Gates
- [ ] A single breach returns `CONFIRMING`, not `ROLLBACK` (flapping guard verified).
- [ ] `consecutive_failures_required` consecutive breaches return `ROLLBACK`.
- [ ] Second rollback within cooldown returns `SUPPRESSED`, and breaches during the cooldown do not advance the streak (the first breach after the cooldown expires returns `CONFIRMING`, not `ROLLBACK`).
- [ ] `max_rollbacks_per_deployment` reached -> confirmed breach gives `SUPPRESSED` + human escalation.
- [ ] `market_open_volatility=True` -> `SUPPRESSED`, streak not advanced.
- [ ] CI/CD controller correctly intercepts `should_rollback` / `decision == ROLLBACK`.

## Rollback Safety (pre-enablement)
- [ ] Standby version adopts outstanding orders and positions; rollback does not abandon in-flight order state.
- [ ] Rollback path exercised in a non-production drill, not only in theory.
- [ ] Rollback policy (thresholds, cap, cooldown, target version) reviewed and approved before the deploy by the person your change-management procedure designates. For an in-scope EU firm this is the RTS 6 Art. 11 review — see `references/standards.md` §1.1.
- [ ] Understood that rollback is **not** a kill switch: it cancels nothing already resting at a venue.

## Monitoring / Escalation
- [ ] On-call page includes version/SHA, anomalies (observed value + threshold), `rollbacks_issued`, streak.
- [ ] Repeated escalation does not auto-resume rollbacks; deployment frozen until human `reset()`.
- [ ] Post-rollback verify window (4–5 min) confirms the rollback fixed the issue.

## Post-Deployment Verification
- [ ] Run `python -m unittest discover -s skills/automated-rollback-triggers-on-anomaly-detection/scripts`.
- [ ] Confirm the engine tracks both technical AND trading metrics with severity classification.
- [ ] Confirm invalid config/metrics raise `ValueError`.
- [ ] File a post-mortem for any rollback that fired.

## Sign-off
- DevOps Engineer: ___________________________
- Quant / Risk Owner: ___________________________
- Date: ___________________________
